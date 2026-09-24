"""Invariants every generated image must hold.

Written as properties over every generator rather than as one test per
framework, so a new framework added later cannot quietly ship an image that
runs as root or bakes a secret into a layer.
"""

from __future__ import annotations

import pytest

from deploypro.domain.detect import detect
from deploypro.domain.dockerfiles import ENV_SECRET_ID, _exec_form

#: One repository per generator, so the properties below cover all of them.
FIXTURES = {
    "next-standalone": {
        "package.json": '{"dependencies":{"next":"15"},"scripts":{"build":"b"}}',
        "next.config.js": "module.exports={output:'standalone'}",
        "package-lock.json": "{}",
    },
    "next-export": {
        "package.json": '{"dependencies":{"next":"15"},"scripts":{"build":"b"}}',
        "next.config.js": "module.exports={output:'export'}",
    },
    "node-server": {
        "package.json": (
            '{"dependencies":{"express":"4"},"scripts":{"start":"node i.js"}}'
        ),
    },
    "vite": {
        "package.json": '{"devDependencies":{"vite":"5"},"scripts":{"build":"b"}}',
        "vite.config.ts": "export default {}",
    },
    "python": {
        "requirements.txt": "fastapi\n",
        "main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
    },
    "go": {"go.mod": "module x\n", "main.go": "package main"},
    "static": {"index.html": "<h1>hi</h1>"},
}


@pytest.fixture(params=sorted(FIXTURES))
def plan(request, repo):
    return detect(repo(FIXTURES[request.param]))


def test_every_image_drops_out_of_root(plan):
    """A compromised site process should not be uid 0 inside its container.

    Combined with `--cap-drop=ALL` and `no-new-privileges` at run time, this is
    what keeps a remote-code-execution bug in one site from being a foothold
    on the host.
    """
    assert "USER " in plan.dockerfile


def test_no_secret_is_ever_baked_into_a_layer(plan):
    """Build-time variables arrive through a BuildKit secret mount, which is
    gone after the RUN that used it. ARG would put every one of them in
    `docker history`."""
    assert "ARG " not in plan.dockerfile
    # Any step that runs the project's own build gets the variables, and gets
    # them from the secret mount. (Cache mounts are also `RUN --mount`, which
    # is why this looks for the build commands rather than for `--mount`.)
    if any(cmd in plan.dockerfile for cmd in (" run build", " run b", "go build")):
        assert f"type=secret,id={ENV_SECRET_ID}" in plan.dockerfile


def test_the_runtime_stage_is_separate_from_the_build_stage(plan):
    """Multi-stage, always. A single-stage image ships the compiler, the
    package manager and their caches to production."""
    assert plan.dockerfile.count("FROM ") >= 2
    assert "AS run" in plan.dockerfile


def test_the_port_is_declared_and_matches_the_plan(plan):
    assert f"EXPOSE {plan.port}" in plan.dockerfile


def test_static_sites_carry_no_language_runtime(repo):
    """The whole point of building a static site is that what serves it needs
    nothing the build needed."""
    plan = detect(repo(FIXTURES["vite"]))
    runtime = plan.dockerfile.split("AS run", 1)[1]
    assert "node" not in runtime.lower()
    assert "npm" not in runtime.lower()


def test_dependencies_install_before_the_source_is_copied(repo):
    """If `COPY . .` came first, every commit would invalidate the install
    layer and every build would reinstall node_modules from scratch."""
    dockerfile = detect(repo(FIXTURES["next-standalone"])).dockerfile
    deps_stage = dockerfile.split("AS build", 1)[0]
    assert "COPY package.json" in deps_stage
    assert "COPY . ." not in deps_stage


def test_dependency_downloads_are_cached_between_builds(plan):
    """Every generator that installs packages does it with a cache mount, so a
    changed lockfile re-downloads what changed rather than everything."""
    build = plan.dockerfile.split("AS run", 1)[0]
    installs = [
        line
        for line in build.splitlines()
        if line.startswith("RUN")
        and any(tool in line for tool in ("npm ci", "npm install", "pip", "go mod"))
    ]
    for line in installs:
        assert "type=cache" in line, line


def test_no_cache_reaches_the_runtime_stage(plan):
    """A cache mount in the runtime stage would mean the runtime stage runs a
    package manager, which is exactly what multi-stage builds exist to avoid."""
    runtime = plan.dockerfile.split("AS run", 1)[1]
    assert "type=cache" not in runtime


def test_pip_is_not_told_to_skip_its_cache(repo):
    """PIP_NO_CACHE_DIR would make the cache mount pointless."""
    assert "PIP_NO_CACHE_DIR" not in detect(repo(FIXTURES["python"])).dockerfile


def test_each_node_toolchain_caches_where_it_actually_downloads():
    from deploypro.domain.dockerfiles import BUN, NPM, PNPM, YARN

    assert {tc.name: tc.cache_dir for tc in (NPM, PNPM, YARN, BUN)} == {
        "npm": "/root/.npm",
        "pnpm": "/root/.local/share/pnpm/store",
        "yarn": "/usr/local/share/.cache/yarn",
        "bun": "/root/.bun/install/cache",
    }


class TestExecForm:
    """CMD form decides whether SIGTERM reaches the process.

    Shell form wraps the command in `/bin/sh -c`, which does not forward
    signals, so every container replacement waits out the full stop timeout
    instead of exiting immediately.
    """

    def test_a_simple_command_becomes_a_json_array(self):
        assert _exec_form("node server.js") == '["node", "server.js"]'

    def test_a_command_needing_a_shell_keeps_one_but_execs_through_it(self):
        rendered = _exec_form("node server.js | tee log")
        assert rendered.startswith('["/bin/sh", "-c", "exec ')


def _caddyfile(plan) -> str | None:
    return dict(plan.context_files).get("Caddyfile")


def test_every_static_site_gets_the_baseline_security_headers(plan):
    """A static site has no server to run a middleware in, and Next's
    `headers()` does nothing under `output: 'export'`. If the platform does
    not set these, nothing does — and a project arriving from a host that set
    them in its own config file loses them without a single error."""
    caddyfile = _caddyfile(plan)
    if caddyfile is None:
        return
    for header in (
        "X-Content-Type-Options nosniff",
        "Referrer-Policy strict-origin-when-cross-origin",
        "X-Frame-Options DENY",
    ):
        assert header in caddyfile


def test_the_server_does_not_announce_itself(plan):
    """Its name and version are of use to nobody but whoever is looking for
    hosts running a version with a published bug."""
    caddyfile = _caddyfile(plan)
    if caddyfile is not None:
        assert "-Server" in caddyfile


def test_an_exported_site_serves_its_own_404_page(repo):
    """Next writes `404.html` during export. Without this Caddy answers a
    missing path with a bare status line and that page is never seen."""
    caddyfile = _caddyfile(detect(repo(FIXTURES["next-export"])))
    assert "handle_errors" in caddyfile
    assert "/404.html" in caddyfile


def test_a_single_page_app_has_no_error_handler(repo):
    """Its fallback already matches every path, so nothing reaches an error
    handler and one would only be dead configuration."""
    caddyfile = _caddyfile(detect(repo(FIXTURES["vite"])))
    assert "handle_errors" not in caddyfile
