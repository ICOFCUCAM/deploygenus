"""A busy host must not fill its own disk, and must say so when it is full.

Regression (BalanceVid, September 2026): the build cache was pruned only when
unused for a week. A project that builds every day keeps its whole cache in
use, so it grew to 20 GB and filled the disk. Postgres could then not write,
and every deploy after failed within seconds with "server closed the
connection unexpectedly" — an error about the database, not the disk.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from deploypro.adapters import db
from deploypro.engine import housekeeping

HOUR = 3600


class TestHousekeeping:
    async def test_it_sweeps_leftover_build_directories(self, monkeypatch, tmp_path):
        """The size cap itself is covered in test_build_cache.py."""

        async def nothing(*_args, **_kwargs):
            return []

        async def zero(*_args, **_kwargs):
            return 0

        async def pruned(**_kwargs):
            return "Total reclaimed space: 0B"

        monkeypatch.setattr(housekeeping, "sweep_images", nothing)
        monkeypatch.setattr(housekeeping.containers, "prune_build_cache", pruned)
        monkeypatch.setattr(housekeeping.deployment_repo, "delete_old_logs", zero)
        monkeypatch.setattr(housekeeping.process_repo, "delete_old_runs", zero)
        leftover = tmp_path / "balancevid-543eac93"
        leftover.mkdir()
        os.utime(leftover, (1, 1))
        settings = SimpleNamespace(
            build_cache_max_gb=8, build_root=tmp_path, log_retention_days=30
        )
        report = await housekeeping.run(settings)
        assert report["build_dirs_removed"] == ["balancevid-543eac93"]
        assert not leftover.exists()


def age(path: Path, seconds: float, now: float) -> None:
    os.utime(path, (now - seconds, now - seconds))


class TestBuildDirectories:
    def test_a_directory_a_stopped_worker_left_is_removed(self, tmp_path):
        now = 1_000_000.0
        (tmp_path / "balancevid-543eac93" / "repo").mkdir(parents=True)
        age(tmp_path / "balancevid-543eac93", 2 * HOUR, now)
        assert housekeeping.sweep_build_dirs(tmp_path, now=now) == ["balancevid-543eac93"]
        assert not (tmp_path / "balancevid-543eac93").exists()

    def test_a_directory_in_use_is_left_alone(self, tmp_path):
        """A promotion from the dashboard writes into one with no build running."""
        now = 1_000_000.0
        (tmp_path / "balancevid-af53004f").mkdir()
        age(tmp_path / "balancevid-af53004f", 60, now)
        assert housekeeping.sweep_build_dirs(tmp_path, now=now) == []
        assert (tmp_path / "balancevid-af53004f").exists()

    def test_job_directories_get_a_day(self, tmp_path):
        now = 1_000_000.0
        jobs = tmp_path / "jobs"
        (jobs / "old-run").mkdir(parents=True)
        (jobs / "recent-run").mkdir()
        age(jobs / "old-run", 25 * HOUR, now)
        age(jobs / "recent-run", 2 * HOUR, now)
        age(jobs, 2 * HOUR, now)
        assert housekeeping.sweep_build_dirs(tmp_path, now=now) == ["jobs/old-run"]
        assert jobs.exists()

    def test_no_build_root_is_nothing_to_do(self, tmp_path):
        assert housekeeping.sweep_build_dirs(tmp_path / "missing") == []


class TestRoomToBuild:
    def settings(self, tmp_path, **kwargs):
        return SimpleNamespace(build_root=tmp_path, min_free_gb=5, **kwargs)

    async def test_with_room_nothing_is_cleared(self, monkeypatch, tmp_path):
        monkeypatch.setattr(housekeeping, "free_gb", lambda _path: 20.0)
        ran = []
        monkeypatch.setattr(housekeeping, "run", lambda s: ran.append(s))
        assert await housekeeping.ensure_room(self.settings(tmp_path)) is None
        assert ran == []

    async def test_a_nearly_full_disk_is_cleared_first(self, monkeypatch, tmp_path):
        free = iter([2.0, 12.0])
        monkeypatch.setattr(housekeeping, "free_gb", lambda _path: next(free))
        ran = []

        async def run(settings):
            ran.append(settings)
            return {}

        monkeypatch.setattr(housekeeping, "run", run)
        assert await housekeeping.ensure_room(self.settings(tmp_path)) is None
        assert len(ran) == 1

    async def test_a_full_disk_fails_the_build_and_says_why(self, monkeypatch, tmp_path):
        monkeypatch.setattr(housekeeping, "free_gb", lambda _path: 1.2)

        async def run(settings):
            return {}

        monkeypatch.setattr(housekeeping, "run", run)
        reason = await housekeeping.ensure_room(self.settings(tmp_path))
        assert reason.startswith("The server's disk is nearly full: 1.2 GB free")
        assert "at least 5 GB" in reason

    async def test_the_guard_can_be_turned_off(self, monkeypatch, tmp_path):
        monkeypatch.setattr(housekeeping, "free_gb", lambda _path: 0.1)
        settings = replace_ns(self.settings(tmp_path), min_free_gb=0)
        assert await housekeeping.ensure_room(settings) is None


def replace_ns(ns: SimpleNamespace, **changes) -> SimpleNamespace:
    return SimpleNamespace(**{**vars(ns), **changes})


class TestSettings:
    def test_defaults(self, monkeypatch):
        from deploypro.config import Settings

        fields = Settings.__dataclass_fields__
        assert fields["min_free_gb"].default == 5


async def test_a_pooled_connection_is_checked_before_use(monkeypatch):
    """After Postgres restarts, every pooled connection is dead. Unchecked,
    the next deploy to use one fails."""
    seen = {}

    class FakePool:
        check_connection = object()

        def __init__(self, dsn, **kwargs):
            seen.update(kwargs)

        async def open(self, wait):
            return None

    monkeypatch.setattr(db, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(db, "_pool", None)
    await db.open_pool("postgresql://unused/unused")
    assert seen["check"] is FakePool.check_connection
    monkeypatch.setattr(db, "_pool", None)


def test_the_settings_are_documented():
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    assert "DEPLOYPRO_MIN_FREE_GB" in readme
    example = (Path(__file__).parent.parent / ".env.example").read_text()
    assert "DEPLOYPRO_MIN_FREE_GB=5" in example
