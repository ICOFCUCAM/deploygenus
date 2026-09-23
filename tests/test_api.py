"""The API's front door.

Exercised through the ASGI app without its lifespan, so these run with no
database: everything here is decided before a handler touches one.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

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
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://deploypro.test"
    ) as http:
        yield http


async def test_health_needs_no_token(client):
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/projects",
        "/api/deployments/blog-abc12345",
        "/api/projects/blog/env",
    ],
)
async def test_every_management_route_refuses_an_anonymous_caller(client, path):
    response = await client.get(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_a_wrong_token_is_refused(client):
    response = await client.get(
        "/api/projects", headers={"Authorization": "Bearer not-the-token"}
    )
    assert response.status_code == 401


async def test_a_token_in_the_wrong_scheme_is_refused(client):
    """Basic auth carrying the right secret is still not a bearer token."""
    response = await client.get("/api/projects", headers={"Authorization": TOKEN})
    assert response.status_code == 401


async def test_errors_come_back_as_structured_json_not_a_stack_trace(client):
    response = await client.get("/api/projects")
    body = response.json()
    assert set(body["error"]) == {"code", "message"}
    assert "Traceback" not in response.text


async def test_the_webhook_secret_is_not_readable_through_the_api(client):
    """A read endpoint for it would mean the API token can retrieve every
    project's signing key."""
    response = await client.get("/webhooks/blog/secret")
    assert response.status_code == 404
    assert "not readable" in response.json()["error"]["message"]


class TestMigrationsDir:
    """The first real install ran `migrate` from an installed package, found no
    migrations beside it, and reported an empty database as up to date."""

    def test_an_explicit_directory_wins(self, tmp_path, monkeypatch):
        from deploypro.cli import migrations_dir

        (tmp_path / "0001_core.sql").write_text("select 1;")
        monkeypatch.setenv("DEPLOYPRO_MIGRATIONS_DIR", str(tmp_path))
        assert migrations_dir() == tmp_path

    def test_finding_none_is_an_error_not_up_to_date(self, tmp_path, monkeypatch):
        import deploypro.cli as cli
        from deploypro.domain.errors import DeployProError

        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setenv("DEPLOYPRO_MIGRATIONS_DIR", str(empty))
        # Pretend to be installed somewhere with no db/ beside the package.
        monkeypatch.setattr(
            cli, "__file__", str(tmp_path / "site-packages/deploypro/cli.py")
        )
        monkeypatch.chdir(tmp_path)
        if Path("/app/db/migrations").exists():
            pytest.skip("this machine has /app/db/migrations")
        with pytest.raises(DeployProError, match="No migration files found"):
            cli.migrations_dir()

    def test_the_image_says_where_its_migrations_are(self):
        dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text()
        assert "ENV DEPLOYPRO_MIGRATIONS_DIR=/app/db/migrations" in dockerfile
        assert "COPY db ./db" in dockerfile
