"""Getting secrets to a build and a container without mangling them."""

from __future__ import annotations

import stat
import subprocess
from dataclasses import replace

from cryptography.fernet import Fernet

from deploypro.adapters import crypto
from deploypro.domain.models import EnvTarget
from deploypro.engine import environment
from deploypro.engine.environment import (
    Environment,
    _split,
    platform_variables,
    write_build_secret,
    write_runtime_env_file,
)
from tests import fakes


def test_a_value_containing_a_quote_cannot_escape_the_shell(tmp_path):
    """The build sources this file. An unescaped value would be executable.

    `hunter2'; rm -rf /; echo '` is the whole attack, and single-quote
    escaping is the whole defence.
    """
    env = _split({"PASSWORD": "hunter2'; rm -rf /; echo '"})
    path = write_build_secret(env, tmp_path / "build.env")
    content = path.read_text()
    assert "rm -rf" in content
    assert content.startswith("PASSWORD='")
    # Proof rather than inspection: ask a shell what it actually reads back.
    result = subprocess.run(
        ["sh", "-c", f'. {path}; printf %s "$PASSWORD"'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "hunter2'; rm -rf /; echo '"


def test_the_runtime_file_is_literal_and_unquoted(tmp_path):
    """`docker --env-file` is not a shell. Quoting a value there would put the
    quotes inside the variable."""
    env = _split({"GREETING": "hello world"})
    path = write_runtime_env_file(env, tmp_path / "runtime.env")
    assert path.read_text() == "GREETING=hello world\n"


def test_multiline_values_are_kept_out_of_the_env_file(tmp_path):
    """A PEM key has newlines, and the env-file format cannot express one —
    Docker would read the second line as a separate, malformed variable."""
    pem = "-----BEGIN KEY-----\nabc\n-----END KEY-----"
    env = _split({"TLS_KEY": pem, "PORT_NAME": "web"})

    assert env.inline == {"TLS_KEY": pem}
    assert env.file_safe == {"PORT_NAME": "web"}

    path = write_runtime_env_file(env, tmp_path / "runtime.env")
    assert "BEGIN KEY" not in path.read_text()
    # But the build, which does source a shell file, still sees it.
    assert "BEGIN KEY" in write_build_secret(env, tmp_path / "b.env").read_text()


def test_both_files_are_created_unreadable_by_anyone_else(tmp_path):
    """These hold every plaintext secret for a project while a build runs."""
    env = _split({"SECRET": "x"})
    for name, write in (
        ("build.env", write_build_secret),
        ("runtime.env", write_runtime_env_file),
    ):
        path = write(env, tmp_path / name)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_an_empty_environment_still_writes_a_sourceable_file(tmp_path):
    path = write_build_secret(Environment({}, {}), tmp_path / "build.env")
    assert path.read_text() == ""


def test_the_platform_tells_a_deployment_its_own_url():
    """An app cannot know its preview hostname at commit time, and needs it
    for canonical tags and OAuth redirects."""
    variables = platform_variables(
        short_id="blog-abc123",
        git_sha="f" * 40,
        url="https://blog-abc123.deploys.example.com",
        target=EnvTarget.PREVIEW,
    )
    assert variables["DEPLOYPRO_URL"] == "https://blog-abc123.deploys.example.com"
    assert variables["DEPLOYPRO_ENV"] == "preview"


class TestScopePrecedence:
    """A variable scoped to one environment beats the same key set for all.

    Regression: `list_env` returns rows ordered by key, then target, and the
    Postgres enum orders targets production < preview < all. Resolving them in
    that order let the `all` value overwrite a production-only one, so a
    production DATABASE_URL set beside a general one was silently ignored.
    """

    KEY = Fernet.generate_key().decode()

    def rows(self, *pairs):
        # In the order the database returns them: by key, then by the enum's
        # declaration order (production, preview, all).
        order = {EnvTarget.PRODUCTION: 0, EnvTarget.PREVIEW: 1, EnvTarget.ALL: 2}
        made = [
            replace(
                fakes.env_var(key, target),
                value_encrypted=crypto.encrypt(value, key=self.KEY),
            )
            for key, target, value in pairs
        ]
        return sorted(made, key=lambda v: (v.key, order[v.target]))

    async def resolve(self, monkeypatch, rows, target):
        monkeypatch.setattr(
            environment.project_repo, "list_env", lambda _id: _async(rows)
        )
        env = await environment.collect(fakes.project().id, target=target, key=self.KEY)
        return env.all

    async def test_a_production_value_beats_the_all_value_in_production(
        self, monkeypatch
    ):
        rows = self.rows(
            ("DATABASE_URL", EnvTarget.ALL, "postgres://shared"),
            ("DATABASE_URL", EnvTarget.PRODUCTION, "postgres://production"),
        )
        resolved = await self.resolve(monkeypatch, rows, EnvTarget.PRODUCTION)
        assert resolved["DATABASE_URL"] == "postgres://production"

    async def test_a_preview_value_beats_the_all_value_in_previews(self, monkeypatch):
        rows = self.rows(
            ("API_URL", EnvTarget.ALL, "https://api.example.com"),
            ("API_URL", EnvTarget.PREVIEW, "https://staging.example.com"),
        )
        resolved = await self.resolve(monkeypatch, rows, EnvTarget.PREVIEW)
        assert resolved["API_URL"] == "https://staging.example.com"

    async def test_the_all_value_still_applies_where_nothing_more_specific_is_set(
        self, monkeypatch
    ):
        rows = self.rows(
            ("DATABASE_URL", EnvTarget.ALL, "postgres://shared"),
            ("DATABASE_URL", EnvTarget.PRODUCTION, "postgres://production"),
        )
        resolved = await self.resolve(monkeypatch, rows, EnvTarget.PREVIEW)
        assert resolved["DATABASE_URL"] == "postgres://shared"

    async def test_a_variable_scoped_elsewhere_is_never_visible(self, monkeypatch):
        rows = self.rows(("SECRET", EnvTarget.PRODUCTION, "prod-only"))
        resolved = await self.resolve(monkeypatch, rows, EnvTarget.PREVIEW)
        assert "SECRET" not in resolved

    async def test_deploypros_own_variables_can_be_overridden_by_the_owner(
        self, monkeypatch
    ):
        """Unchanged behaviour, pinned: a project variable replaces an injected
        one of the same name."""
        rows = self.rows(("DEPLOYPRO_URL", EnvTarget.ALL, "https://custom.example"))
        monkeypatch.setattr(
            environment.project_repo, "list_env", lambda _id: _async(rows)
        )
        env = await environment.collect(
            fakes.project().id,
            target=EnvTarget.PRODUCTION,
            key=self.KEY,
            injected={"DEPLOYPRO_URL": "https://injected.example"},
        )
        assert env.all["DEPLOYPRO_URL"] == "https://custom.example"


async def _async(value):
    return value
