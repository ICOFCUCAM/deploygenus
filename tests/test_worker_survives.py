"""The worker keeps running when a deploy or a job step raises.

Regression (BalanceVid, September 2026): a full disk made Postgres refuse a
write during a deploy. The error escaped the deploy loop and the worker
stopped for good, so every deployment after it waited in the queue with
nothing to pick it up.
"""

from __future__ import annotations

import asyncio

from deploypro import worker as worker_module
from deploypro.worker import Worker


def bare_worker() -> Worker:
    """A Worker without settings or a database: only what the loop reads."""
    w = object.__new__(Worker)
    w._stopping = asyncio.Event()
    return w


async def test_an_error_in_a_step_is_survived(monkeypatch):
    monkeypatch.setattr(worker_module, "ERROR_BACKOFF_SECONDS", 0.01)
    w = bare_worker()
    calls = []

    async def step():
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError(
                'could not extend file "base/16384/16532": No space left on device'
            )
        w._stopping.set()

    await asyncio.wait_for(w._keep_going("deploy", step), timeout=2)
    assert calls == [0, 1]


async def test_it_still_stops_when_asked(monkeypatch):
    monkeypatch.setattr(worker_module, "ERROR_BACKOFF_SECONDS", 60)
    w = bare_worker()

    async def step():
        w._stopping.set()
        raise RuntimeError("database is restarting")

    # Stopping during the pause after an error is immediate, not a minute.
    await asyncio.wait_for(w._keep_going("deploy", step), timeout=2)


async def test_both_loops_are_guarded():
    w = bare_worker()
    seen = []

    async def keep_going(name, step):
        seen.append((name, step))

    w._keep_going = keep_going
    await w._deploy_loop()
    await w._job_loop()
    assert seen == [("deploy", w._deploy_once), ("job", w._job_once)]
