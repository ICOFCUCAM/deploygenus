"""The live log stream, and the state events that ride on it.

The deployment page keeps its header and deployment line current from these
events instead of reloading, which would restart the log (docs/design,
Phase 5 §3.3). So: one event per change, none when nothing changed, and
`done` still ends the stream.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from deploypro.domain.models import DeploymentStatus
from deploypro.routers import deployments as router
from tests import fakes

S = DeploymentStatus


@pytest.fixture
def world(monkeypatch):
    """A deployment whose status moves through a script, one step per poll."""
    base = fakes.deployment(
        status=S.QUEUED, started_at=None, built_at=None, ready_at=None, finished_at=None
    )
    script = {"statuses": [], "logs": []}

    def at(status):
        stamps = {}
        if status in (S.BUILDING, S.DEPLOYING, S.READY, S.FAILED):
            stamps["started_at"] = fakes.NOW
        if status in (S.DEPLOYING, S.READY):
            stamps["built_at"] = fakes.NOW
        if status is S.READY:
            stamps["ready_at"] = stamps["finished_at"] = fakes.NOW
        if status is S.FAILED:
            stamps["finished_at"] = fakes.NOW
        return replace(base, status=status, **stamps)

    async def by_short_id(_short_id):
        return base

    async def get(_id):
        statuses = script["statuses"]
        return at(statuses.pop(0) if len(statuses) > 1 else statuses[0])

    async def read_logs(_id, after=0, limit=0):
        logs, script["logs"] = script["logs"], []
        return logs

    monkeypatch.setattr(router.deployment_repo, "get_by_short_id", by_short_id)
    monkeypatch.setattr(router.deployment_repo, "get", get)
    monkeypatch.setattr(router.deployment_repo, "read_logs", read_logs)
    monkeypatch.setattr(router, "STREAM_POLL_SECONDS", 0)
    monkeypatch.setattr(router, "STREAM_IDLE_LIMIT", 2)
    return script


async def events(seen: str = "") -> list[tuple[str, dict]]:
    response = await router.stream_logs("blog-3f9a2c71", after=0, seen=seen)
    found = []
    async for chunk in response.body_iterator:
        kind = "message"
        data = ""
        for line in chunk.strip().splitlines():
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        found.append((kind, json.loads(data)))
    return found


async def test_each_change_of_state_is_sent_once(world):
    world["statuses"] = [S.QUEUED, S.BUILDING, S.BUILDING, S.DEPLOYING, S.READY]
    found = await events(seen="queued")
    states = [data["status"] for kind, data in found if kind == "state"]
    assert states == ["building", "deploying", "ready"]


async def test_done_still_ends_the_stream(world):
    world["statuses"] = [S.BUILDING, S.READY]
    found = await events(seen="building")
    assert found[-1][0] == "done"
    assert found[-1][1]["status"] == "ready"


async def test_a_change_before_the_stream_opened_is_sent_at_once(world):
    """The page rendered `queued`; by the time the browser connected the
    build had started. The page must not keep saying Queued."""
    world["statuses"] = [S.BUILDING, S.BUILDING, S.FAILED]
    found = await events(seen="queued")
    first_state = next(data for kind, data in found if kind == "state")
    assert first_state["status"] == "building"
    assert first_state["started_at"] is not None
    assert first_state["built_at"] is None


async def test_nothing_is_sent_when_nothing_changed(world):
    world["statuses"] = [S.READY]
    found = await events(seen="ready")
    assert [kind for kind, _ in found] == ["done"]


async def test_log_lines_still_arrive_as_plain_messages(world):
    world["statuses"] = [S.READY]
    world["logs"] = [fakes.log_line(1, "npm run build")]
    found = await events(seen="ready")
    assert found[0] == (
        "message",
        {
            "seq": 1,
            "stream": "build",
            "line": "npm run build",
            "at": fakes.NOW.isoformat(),
        },
    )
