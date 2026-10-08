"""A Dockerfile in a subfolder, built from the repository root.

docs/design/plan-replace-render.md, G1. The shape of both repositories that
leave Render: `services/dispatch-api/Dockerfile` (SOVEREIGN) and
`apps/worker/Dockerfile` (media), each built with `dockerContext: .` so its
`COPY packages/…` lines reach shared folders.
"""

# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments

from __future__ import annotations

from dataclasses import replace

import pytest

from deploypro.domain.detect import explicit_dockerfile, normalise_dockerfile_path
from deploypro.domain.errors import DetectionFailed, InvalidRequest
from deploypro.engine import pipeline
from tests import fakes
from tests.test_dashboard import anon, app, client, repos  # noqa: F401


class TestPath:
    @pytest.mark.parametrize(
        ("given", "kept"),
        [
            ("services/dispatch-api/Dockerfile", "services/dispatch-api/Dockerfile"),
            ("./apps/worker/Dockerfile", "apps/worker/Dockerfile"),
            ("  Dockerfile.prod ", "Dockerfile.prod"),
            ("", ""),
        ],
    )
    def test_a_path_in_the_repository_is_kept(self, given, kept):
        assert normalise_dockerfile_path(given) == kept

    @pytest.mark.parametrize(
        "given",
        [
            "/etc/passwd",
            "../other/Dockerfile",
            "apps/../../x",
            "apps//Dockerfile",
            "apps/Dockerfile/",
            "a b/Dockerfile",
            "apps/./Dockerfile",
        ],
    )
    def test_anything_outside_or_odd_is_refused(self, given):
        with pytest.raises(InvalidRequest, match="relative to the repository root"):
            normalise_dockerfile_path(given)


def monorepo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "services" / "api").mkdir(parents=True)
    (repo / "packages" / "shared").mkdir(parents=True)
    (repo / "services" / "api" / "Dockerfile").write_text(
        "FROM node:20-slim\nCOPY packages/ ./packages/\nEXPOSE 8787\n"
    )
    return repo


class TestPlan:
    def test_the_named_dockerfile_is_used_unchanged_with_its_port(self, tmp_path):
        plan = explicit_dockerfile(
            monorepo(tmp_path), "services/api/Dockerfile", port=None
        )
        assert plan.from_repo and plan.port == 8787
        assert "COPY packages/" in plan.dockerfile
        assert plan.reason.startswith("services/api/Dockerfile in the repository")

    def test_a_missing_file_says_which(self, tmp_path):
        with pytest.raises(DetectionFailed, match="services/web/Dockerfile"):
            explicit_dockerfile(monorepo(tmp_path), "services/web/Dockerfile", port=None)

    def test_a_link_out_of_the_checkout_is_refused(self, tmp_path):
        repo = monorepo(tmp_path)
        (tmp_path / "secret").write_text("FROM scratch\n")
        (repo / "Dockerfile.evil").symlink_to(tmp_path / "secret")
        with pytest.raises(DetectionFailed):
            explicit_dockerfile(repo, "Dockerfile.evil", port=None)

    async def test_the_pipeline_builds_from_the_root_with_it(self, tmp_path):
        repo = monorepo(tmp_path)
        project = replace(fakes.project(), dockerfile_path="services/api/Dockerfile")
        said = []

        class Log:
            async def system(self, line):
                said.append(line)

        plan = await pipeline._plan(project, repo, repo_dir=repo, log=Log())
        assert plan.port == 8787 and "COPY packages/" in plan.dockerfile
        assert said[0].startswith("services/api/Dockerfile")

    async def test_without_one_detection_is_unchanged(self, tmp_path):
        repo = monorepo(tmp_path)
        (repo / "Dockerfile").write_text("FROM scratch\nEXPOSE 9000\n")

        class Log:
            async def system(self, line):
                pass

        plan = await pipeline._plan(fakes.project(), repo, repo_dir=repo, log=Log())
        assert plan.port == 9000


class TestSettings:
    async def test_the_build_page_saves_it_tidied(self, client, repos, monkeypatch):
        saved = {}

        async def update(_id, changes):
            saved.update(changes)

        monkeypatch.setattr("deploypro.web.routes.project_repo.update", update)
        await client.post(
            "/projects/blog/config/build",
            data={"dockerfile_path": "./services/dispatch-api/Dockerfile"},
        )
        assert saved["dockerfile_path"] == "services/dispatch-api/Dockerfile"

    async def test_a_bad_path_is_refused_and_nothing_saved(
        self, client, repos, monkeypatch
    ):
        saved = {}

        async def update(_id, changes):
            saved.update(changes)

        monkeypatch.setattr("deploypro.web.routes.project_repo.update", update)
        response = await client.post(
            "/projects/blog/config/build", data={"dockerfile_path": "../x/Dockerfile"}
        )
        assert "err=" in response.headers["location"] and saved == {}

    async def test_the_page_shows_it(self, client, repos):
        repos["projects"][0] = replace(
            repos["projects"][0], dockerfile_path="apps/worker/Dockerfile"
        )
        body = (await client.get("/projects/blog/config/build")).text
        assert 'name="dockerfile_path"' in body and "apps/worker/Dockerfile" in body
