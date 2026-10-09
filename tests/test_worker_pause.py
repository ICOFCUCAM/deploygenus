"""Pausing and resuming a worker act at once.

October 2026: pausing BalanceVid's playout worker to free an overloaded
server said "pauses at the next deploy", so it took `docker stop` on the
server to actually stop it. Now the button does it, and touches only that
worker.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deploypro.engine import processes
from tests import fakes

SETTINGS = SimpleNamespace(
    network="deploypro",
    master_key="unused",
    deployment_url=lambda short: f"https://{short}.example",
)


class Log:
    def __init__(self):
        self.lines: list[str] = []

    async def system(self, line):
        self.lines.append(line)


@pytest.fixture
def docker(monkeypatch):
    seen = SimpleNamespace(drained=[], started=[], running=set())

    async def drain(name, *, grace):
        seen.drained.append((name, grace))

    async def state(name):
        return "running" if name in seen.running else "exited"

    async def run_worker(spec):
        seen.started.append((spec.name, spec.image, spec.command))
        return "cid"

    async def collect(*a, **k):
        return SimpleNamespace(file_safe={}, inline={})

    async def mounts(*a, **k):
        return ()

    async def production(project):
        return fakes.deployment(image_tag="deploypro/blog:abc")

    monkeypatch.setattr(processes.containers, "drain", drain)
    monkeypatch.setattr(processes.containers, "state", state)
    monkeypatch.setattr(processes.containers, "run_worker", run_worker)
    monkeypatch.setattr(processes.environment, "collect", collect)
    monkeypatch.setattr(processes.storage, "mounts_for", mounts)
    monkeypatch.setattr(processes, "_production_deployment", production)
    return seen


async def apply(process, tmp_path, project=None):
    log = Log()
    count = await processes.apply_worker(
        project or fakes.project(stop_timeout_seconds=30),
        process,
        settings=SETTINGS,
        log=log,
        workdir=tmp_path,
    )
    return count, log


class TestPause:
    async def test_every_replica_is_drained_with_the_shutdown_time(
        self, docker, tmp_path
    ):
        count, log = await apply(
            fakes.process(name="playout", enabled=False, replicas=2), tmp_path
        )
        assert count == 2
        assert docker.drained == [
            ("deploypro-blog-playout-0", 30),
            ("deploypro-blog-playout-1", 30),
        ]
        assert docker.started == []
        assert all("paused" in line for line in log.lines)


class TestResume:
    async def test_it_starts_on_the_production_image(self, docker, tmp_path):
        count, log = await apply(fakes.process(name="render"), tmp_path)
        assert count == 1
        assert docker.started == [
            ("deploypro-blog-render-0", "deploypro/blog:abc", "node worker.js")
        ]
        assert docker.drained == []  # nothing else is touched
        assert "resumed worker deploypro-blog-render-0" in log.lines[0]

    async def test_a_replica_already_running_is_left_alone(self, docker, tmp_path):
        docker.running = {"deploypro-blog-render-0"}
        count, _ = await apply(fakes.process(name="render", replicas=2), tmp_path)
        assert count == 1
        assert [name for name, *_ in docker.started] == ["deploypro-blog-render-1"]

    async def test_with_nothing_in_production_it_waits_for_the_first_deploy(
        self, docker, tmp_path, monkeypatch
    ):
        async def none(project):
            return None

        monkeypatch.setattr(processes, "_production_deployment", none)
        count, _ = await apply(fakes.process(), tmp_path)
        assert count == 0 and docker.started == []
