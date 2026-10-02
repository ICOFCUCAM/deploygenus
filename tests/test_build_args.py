"""Build arguments: a Dockerfile's ARG switches, set per project.

Why they exist: BalanceVid's Dockerfile installs a capable ffmpeg only with
WITH_TEXT=1, and DeployPro had no way to say so — environment variables
reach a build as a secret mount, never as an ARG. Build arguments are
visible in the image, so a name that looks like a secret is refused.
"""

# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments

from __future__ import annotations

from dataclasses import replace

import pytest

from deploypro.adapters import containers
from deploypro.domain import build_args
from deploypro.domain.errors import InvalidRequest
from tests.test_dashboard import anon, app, client, repos  # noqa: F401


class TestRules:
    def test_lines_become_arguments_in_order(self):
        text = "# capable ffmpeg\nWITH_TEXT=1\n\nWITH_BROWSER = 0\nNOTE=a=b c\n"
        assert build_args.parse(text) == {
            "WITH_TEXT": "1",
            "WITH_BROWSER": "0",
            "NOTE": "a=b c",
        }

    def test_stored_form_round_trips(self):
        args = {"WITH_TEXT": "1", "EMPTY": ""}
        assert build_args.parse(build_args.to_text(args)) == args

    @pytest.mark.parametrize(
        "line", ["WITH_TEXT", "1ST=x", "WITH-TEXT=1", "A B=1", "x" * 129 + "=1"]
    )
    def test_a_malformed_line_is_refused_with_its_number(self, line):
        with pytest.raises(InvalidRequest, match="Line 2"):
            build_args.parse(f"OK=1\n{line}")

    @pytest.mark.parametrize(
        "name",
        ["DB_PASSWORD", "GITHUB_TOKEN", "API_KEY", "STRIPE_SECRET_KEY", "PRIVATE_PEM"],
    )
    def test_a_secret_name_is_sent_to_environment(self, name):
        with pytest.raises(InvalidRequest, match="Environment"):
            build_args.parse(f"{name}=x")

    @pytest.mark.parametrize(
        "name", ["WITH_TEXT", "KEYFRAMES", "NODE_ENV", "TOKENIZER_X"]
    )
    def test_ordinary_switches_are_not_mistaken_for_secrets(self, name):
        assert build_args.parse(f"{name}=1") == {name: "1"}

    def test_too_many_is_refused(self):
        many = "\n".join(f"A{i}=1" for i in range(build_args.MAX_ARGS + 1))
        with pytest.raises(InvalidRequest, match="At most"):
            build_args.parse(many)


class TestBuild:
    async def test_each_argument_reaches_docker_build(self, monkeypatch, tmp_path):
        seen = {}

        async def stream(args, *, log, timeout, buildkit):
            seen["args"] = args

        monkeypatch.setattr(containers, "_stream", stream)

        async def log(_line):
            pass

        await containers.build(
            context=tmp_path,
            dockerfile=tmp_path / "Dockerfile",
            tag="deploypro/bv:abc",
            secret_env_file=tmp_path / "env",
            log=log,
            timeout=60,
            build_args={"WITH_TEXT": "1", "WITH_OFFICE": "0"},
        )
        args = seen["args"]
        pairs = [args[i + 1] for i, a in enumerate(args) if a == "--build-arg"]
        assert pairs == ["WITH_TEXT=1", "WITH_OFFICE=0"]
        # Variables stay a secret mount, never an argument.
        assert args[args.index("--secret") + 1].startswith("id=env,src=")
        assert args[-1] == str(tmp_path)


class TestPage:
    async def test_the_build_page_shows_them(self, client, repos):
        repos["projects"][0] = replace(repos["projects"][0], build_args="WITH_TEXT=1\n")
        body = (await client.get("/projects/blog/config/build")).text
        assert 'name="build_args"' in body and "WITH_TEXT=1" in body
        assert "never a password" in body

    async def test_saving_stores_them_tidied(self, client, repos, monkeypatch):
        saved = {}

        async def update(_id, changes):
            saved.update(changes)

        monkeypatch.setattr("deploypro.web.routes.project_repo.update", update)
        response = await client.post(
            "/projects/blog/config/build", data={"build_args": " WITH_TEXT = 1 \r\n\r\n"}
        )
        assert "ok=" in response.headers["location"]
        assert saved["build_args"] == "WITH_TEXT=1\n"

    async def test_a_secret_is_refused_and_nothing_saved(
        self, client, repos, monkeypatch
    ):
        saved = {}

        async def update(_id, changes):
            saved.update(changes)

        monkeypatch.setattr("deploypro.web.routes.project_repo.update", update)
        response = await client.post(
            "/projects/blog/config/build", data={"build_args": "DB_PASSWORD=hunter2"}
        )
        assert "err=" in response.headers["location"]
        assert "hunter2" not in response.headers["location"]
        assert saved == {}
