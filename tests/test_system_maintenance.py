"""Disk and backups, and the setup checklist, on the System page.

docs/design/proposal-system-disk-and-backups.md, with the owner's
recommended answers to D1–D4. Normal is quiet: a mark appears only when
something needs attention.
"""

# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploypro.domain.maintenance import (
    GB,
    Disk,
    MaintenanceRun,
    describe_cleanup,
    parse_size,
)
from deploypro.engine import disks
from deploypro.web import views
from tests.test_dashboard import anon, app, client, repos  # noqa: F401

NOW = datetime(2026, 9, 30, 14, 0, tzinfo=UTC)


def run(kind="backup", status="succeeded", **kw) -> MaintenanceRun:
    return MaintenanceRun(1, kind, "schedule", status, NOW - timedelta(hours=1), **kw)


# ---------------------------------------------------------------------------
# Sizes and sentences
# ---------------------------------------------------------------------------


class TestSizes:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Total reclaimed space: 1.8GB", 1_800_000_000),
            ("Total reclaimed space: 0B", 0),
            ("512MB", 512_000_000),
            ("3.2 GiB", int(3.2 * 1024**3)),
            ("nothing here", None),
        ],
    )
    def test_docker_sizes_are_read(self, text, expected):
        assert parse_size(text) == expected

    def test_a_clean_up_says_what_it_removed(self):
        report = {
            "images_removed": ["deploypro/vid:aaa", "deploypro/vid:bbb"],
            "build_cache": "Total reclaimed space: 1.2GB | Total reclaimed space: 600MB",
            "build_dirs_removed": [],
        }
        assert describe_cleanup(report) == "2 images and 1.8 GB of build cache removed"

    def test_a_clean_up_that_found_nothing_says_so(self):
        report = {"images_removed": [], "build_cache": "Total reclaimed space: 0B"}
        assert describe_cleanup(report) == "nothing to remove"

    def test_a_step_that_failed_is_named(self):
        report = {"images_removed": [], "images_error": "docker is busy"}
        assert describe_cleanup(report) == "nothing to remove; could not clear images"


# ---------------------------------------------------------------------------
# Disks
# ---------------------------------------------------------------------------


def disk(percent: int, total_gb: int = 40) -> Disk:
    used = total_gb * GB * percent // 100
    return Disk("Server disk", total_gb * GB, used, total_gb * GB - used)


class TestDiskFacts:
    def test_a_disk_with_room_is_quiet(self):
        fact = views.disk_fact(disk(20), alert_percent=90, min_free_gb=5)
        assert fact.mark is None
        assert fact.text == "20% used · 32.0 GB free of 40 GB"

    def test_a_disk_past_the_alert_threshold_is_a_caution(self):
        fact = views.disk_fact(disk(91, 100), alert_percent=90, min_free_gb=5)
        assert fact.mark.shape == "caution" and fact.mark.word == "91% used"

    def test_a_disk_too_full_to_build_on_says_builds_are_refused(self):
        fact = views.disk_fact(disk(97), alert_percent=90, min_free_gb=5)
        assert fact.mark.shape == "failed"
        assert fact.note == "Builds are refused until there is 5 GB free."

    def test_the_percentage_is_dfs(self):
        """Used over used-plus-available: root's reserve counts as neither."""
        d = Disk("Disk", total=100 * GB, used=40 * GB, free=40 * GB)
        assert d.used_percent == 50


class TestWhichDisks:
    def settings(self, tmp_path, store):
        return SimpleNamespace(build_root=tmp_path, image_store_dir=store)

    def test_one_disk_is_shown_once(self, tmp_path):
        store = tmp_path / "images"
        store.mkdir()
        found = disks.measure(self.settings(tmp_path, store))
        assert [d.label for d in found] == ["Disk"]
        assert [name for name, _ in disks.paths(self.settings(tmp_path, store))] == [
            "the server's disk"
        ]

    def test_a_missing_image_store_is_not_an_error(self, tmp_path):
        found = disks.measure(self.settings(tmp_path, tmp_path / "missing"))
        assert [d.label for d in found] == ["Disk"]

    def test_an_image_store_on_its_own_disk_is_its_own_row(self, tmp_path, monkeypatch):
        store = tmp_path / "images"
        store.mkdir()
        real_stat = os.stat

        def stat(path, *a, **kw):
            result = real_stat(path, *a, **kw)
            if Path(path) == store:
                return SimpleNamespace(st_dev=result.st_dev + 1)
            return result

        monkeypatch.setattr(disks.os, "stat", stat)
        settings = self.settings(tmp_path, store)
        assert [d.label for d in disks.measure(settings)] == [
            "Server disk",
            "Image storage",
        ]
        assert [name for name, _ in disks.paths(settings)] == [
            "the server's disk",
            "the image storage disk",
        ]


# ---------------------------------------------------------------------------
# Clean-ups and backups
# ---------------------------------------------------------------------------


class TestCleanupFact:
    def test_the_last_one_and_what_it_did(self):
        last = run(
            "cleanup",
            finished_at=NOW - timedelta(minutes=20),
            summary="nothing to remove",
        )
        fact = views.cleanup_fact(None, last, NOW)
        assert fact.mark is None
        assert fact.text == "13:40 · nothing to remove · hourly"

    def test_one_running(self):
        fact = views.cleanup_fact(run("cleanup", "running", started_at=NOW), None, NOW)
        assert fact.mark.shape == "building" and fact.text == "started 14:00"

    def test_one_asked_for_and_not_yet_picked_up(self):
        fact = views.cleanup_fact(run("cleanup", "requested"), None, NOW)
        assert fact.text == "waiting for the worker"

    def test_a_failed_one_says_why(self):
        last = run("cleanup", "failed", finished_at=NOW, error="docker is busy")
        fact = views.cleanup_fact(None, last, NOW)
        assert fact.mark.shape == "failed" and "docker is busy" in fact.text


def backups(state=None, open_run=None, last=None, enabled=True, now=NOW):
    return views.backups(state, open_run, last, enabled=enabled, hour=3, now=now)


class TestBackups:
    def state(self, hours_ago):
        return {
            "dir": "/var/backups/deploypro",
            "count": 7,
            "newest": "deploypro-x",
            "newest_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "newest_bytes": 1_200_000_000,
        }

    def test_a_recent_backup_is_quiet(self):
        view = backups(self.state(11))
        assert view.facts[0].mark is None
        assert view.facts[0].text == "03:00 · 1.2 GB · 7 kept"
        assert not view.needed and view.can_start

    def test_none_yet_is_a_caution_and_makes_backing_up_the_next_action(self):
        view = backups({"dir": "/var/backups/deploypro", "count": 0})
        assert view.facts[0].mark.word == "None yet"
        assert view.needed

    def test_an_old_backup_is_a_caution(self):
        view = backups(self.state(30))
        assert view.facts[0].mark.word == "Not recent" and view.needed

    def test_a_failure_after_the_newest_backup_shows(self):
        last = run("backup", "failed", finished_at=NOW, error="disk full")
        view = backups(self.state(11), last=last)
        assert view.facts[0].mark.shape == "failed" and "disk full" in view.facts[0].text

    def test_a_failure_before_the_newest_backup_is_history(self):
        last = run("backup", "failed", finished_at=NOW - timedelta(hours=20), error="x")
        assert backups(self.state(11), last=last).facts[0].mark is None

    def test_one_running_hides_the_button(self):
        view = backups(self.state(30), open_run=run("backup", "running", started_at=NOW))
        assert view.facts[0].mark.shape == "building"
        assert not view.can_start and not view.needed

    def test_the_next_one(self):
        assert backups(self.state(11)).facts[1].text == "Tomorrow 03:00 UTC"
        early = NOW.replace(hour=1)
        assert backups(self.state(11), now=early).facts[1].text == "Today 03:00 UTC"

    def test_where_they_are_kept_is_always_said(self):
        kept = backups(self.state(11)).facts[2]
        assert kept.text == "This server only (/var/backups/deploypro)"
        assert "lost with the server" in kept.note

    def test_backups_off(self):
        view = backups(enabled=False)
        assert view.facts[0].mark.word == "Off" and not view.can_start


class TestAlerts:
    def test_off_is_a_caution(self):
        assert views.alerts_fact("").mark.word == "Off"

    @pytest.mark.parametrize(
        ("url", "kind"),
        [
            ("https://discord.com/api/webhooks/1/secret", "Discord"),
            ("https://hooks.slack.com/services/T/B/secret", "Slack"),
            ("https://example.com/hook", "webhook"),
        ],
    )
    def test_on_names_the_service_and_never_the_url(self, url, kind):
        fact = views.alerts_fact(url)
        assert fact.text == f"On · {kind}" and "secret" not in fact.text + fact.note


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


@pytest.fixture
def backups_on(app):
    from deploypro.config import get_settings
    from deploypro.deps import settings_dep

    settings = replace(
        get_settings(),
        backup_dir=Path("/var/backups/deploypro"),
        alert_webhook_url="https://discord.com/api/webhooks/1/do-not-show-me",
    )
    app.dependency_overrides[settings_dep] = lambda: settings
    yield settings
    app.dependency_overrides.pop(settings_dep, None)


class TestPage:
    async def test_it_shows_disk_and_backups_and_setup(self, client, repos):
        body = (await client.get("/system")).text
        assert "Disk and backups" in body and "Setup" in body
        assert "Clean up now" in body
        assert 'http-equiv="refresh"' not in body

    async def test_the_webhook_url_is_never_on_the_page(self, client, repos, backups_on):
        body = (await client.get("/system")).text
        assert "On · Discord" in body and "do-not-show-me" not in body
        assert "Send test alert" in body

    async def test_backing_up_asks_the_worker_and_the_page_follows_it(
        self, client, repos, backups_on
    ):
        response = await client.post("/system/backup")
        assert "Backup+started" in response.headers["location"].replace("%20", "+")
        body = (await client.get("/system")).text
        assert "Backing up" in body and "Back up now</button>" not in body
        # Proposal D2: it refreshes itself while one runs, and only then.
        assert 'http-equiv="refresh"' in body

    async def test_a_second_backup_is_refused_while_one_runs(
        self, client, repos, backups_on
    ):
        await client.post("/system/backup")
        response = await client.post("/system/backup")
        assert "already+running" in response.headers["location"].replace("%20", "+")
        assert len(repos["maintenance"]) == 1

    async def test_backing_up_with_backups_off_says_how_to_turn_them_on(
        self, client, repos
    ):
        response = await client.post("/system/backup")
        assert "DEPLOYPRO_BACKUP_DIR" in response.headers["location"]
        assert repos["maintenance"] == []

    async def test_cleaning_up_asks_the_worker(self, client, repos):
        response = await client.post("/system/cleanup")
        assert response.headers["location"].startswith("/system?ok=")
        assert [r.kind for r in repos["maintenance"]] == ["cleanup"]

    async def test_a_test_alert_needs_a_webhook(self, client, repos):
        response = await client.post("/system/test-alert")
        assert "err=" in response.headers["location"]

    async def test_no_backup_makes_back_up_now_primary_once_github_is_connected(
        self, client, repos, backups_on, monkeypatch
    ):
        from deploypro.repositories import github as github_repo

        body = (await client.get("/system")).text
        # No app: Connect GitHub is the page's one primary action.
        assert 'btn btn-primary" type="submit">Back up now' not in body

        app_row = SimpleNamespace(html_url="https://github.com/apps/x", slug="x")
        monkeypatch.setattr(github_repo, "get_app", lambda: _async(app_row))
        monkeypatch.setattr(github_repo, "list_installations", lambda: _async([]))
        monkeypatch.setattr(
            "deploypro.engine.github.install_url", lambda app: "https://github.com/x"
        )
        body = (await client.get("/system")).text
        assert 'btn btn-primary" type="submit">Back up now' in body


class TestSetup:
    async def test_the_owner_ticks_what_only_they_can_confirm(self, client, repos):
        body = (await client.get("/system")).text
        assert "4 of 4 left" in body

        response = await client.post("/system/setup/master_key", data={"done": "1"})
        assert response.headers["location"] == "/system"
        body = (await client.get("/system")).text
        assert "3 of 4 left" in body

        await client.post("/system/setup/master_key", data={"done": "0"})
        assert "4 of 4 left" in (await client.get("/system")).text

    async def test_alerts_and_a_backup_are_read_not_ticked(self, client, repos):
        response = await client.post("/system/setup/alerts", data={"done": "1"})
        assert "err=" in response.headers["location"]

    def test_a_taken_backup_and_alerts_count_as_done(self):
        items = views.setup_items(
            {"checklist:offsite": {}},
            alerts_on=True,
            backup_exists=True,
            backup_dir="/var/backups/deploypro",
        )
        assert {i.key: i.done for i in items} == {
            "master_key": False,
            "alerts": True,
            "backup": True,
            "offsite": True,
        }


async def _async(value):
    return value


# ---------------------------------------------------------------------------
# The engine and the worker
# ---------------------------------------------------------------------------


@pytest.fixture
def recorded(monkeypatch):
    """The maintenance repository, recording what the engine told it."""
    from deploypro.repositories import maintenance as repo

    calls = {"finish": [], "fail": [], "state": {}}

    async def finish(run_id, *, summary, detail):
        calls["finish"].append((run_id, summary, detail))

    async def fail(run_id, *, error):
        calls["fail"].append((run_id, error))

    async def set_state(key, value):
        calls["state"][key] = value

    async def nothing(*a, **kw):
        return 0

    monkeypatch.setattr(repo, "finish", finish)
    monkeypatch.setattr(repo, "fail", fail)
    monkeypatch.setattr(repo, "set_state", set_state)
    monkeypatch.setattr(repo, "delete_old", nothing)
    return calls


class TestEngine:
    async def test_a_clean_up_is_recorded_with_its_sentence(self, recorded, monkeypatch):
        from deploypro.engine import maintenance

        async def housekeeping_run(settings):
            return {"images_removed": ["a"], "build_cache": "Total reclaimed space: 0B"}

        monkeypatch.setattr(maintenance.housekeeping, "run", housekeeping_run)
        monkeypatch.setattr(
            maintenance.containers, "build_cache_bytes", lambda: _async(3_100_000_000)
        )
        await maintenance.run_cleanup(SimpleNamespace(), run("cleanup", "running"))
        assert recorded["finish"][0][1] == "1 image removed"
        assert recorded["state"]["build_cache"] == {"bytes": 3_100_000_000}

    async def test_a_clean_up_where_every_step_failed_is_a_failure(
        self, recorded, monkeypatch
    ):
        from deploypro.engine import maintenance

        report = {
            "images_error": "a",
            "build_cache_error": "b",
            "build_dirs_error": "c",
            "retention_error": "d",
        }
        monkeypatch.setattr(maintenance.housekeeping, "run", lambda s: _async(report))
        monkeypatch.setattr(
            maintenance.containers, "build_cache_bytes", lambda: _async(None)
        )
        await maintenance.run_cleanup(SimpleNamespace(), run("cleanup", "running"))
        assert recorded["finish"] == [] and recorded["fail"][0][1] == "a; b; c; d"

    async def test_a_backup_with_backups_off_fails_and_says_why(self, recorded):
        from deploypro.engine import maintenance

        await maintenance.run_backup(SimpleNamespace(backup_dir=None), run())
        assert "DEPLOYPRO_BACKUP_DIR" in recorded["fail"][0][1]

    async def test_the_backup_directory_is_published_for_the_dashboard(
        self, recorded, tmp_path
    ):
        """Backups taken before runs were recorded still count."""
        from deploypro.engine import maintenance

        for name in ("deploypro-20260929T030000Z", "deploypro-20260930T030000Z"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "deploypro.dump").write_bytes(b"x" * 10)
        (tmp_path / "deploypro-20260930T040000Z.partial").mkdir()
        await maintenance.publish_backups(SimpleNamespace(backup_dir=tmp_path))
        fact = recorded["state"]["backups"]
        assert fact["count"] == 2 and fact["newest"] == "deploypro-20260930T030000Z"
        assert fact["newest_at"] == "2026-09-30T03:00:00+00:00"
        assert fact["newest_bytes"] == 10


class TestWorker:
    def worker(self, monkeypatch):
        import asyncio

        from deploypro import worker as worker_module
        from deploypro.worker import Worker

        w = object.__new__(Worker)
        w._stopping = asyncio.Event()
        w._settings = SimpleNamespace(backup_dir=None)
        w._id = "host/1"
        w._last_housekeeping = float("-inf")  # an hour has long passed
        return w, worker_module

    async def test_the_hourly_clean_up_is_recorded(self, monkeypatch):
        w, module = self.worker(monkeypatch)
        begun, ran = [], []

        async def begin(kind, *, origin, worker):
            begun.append((kind, origin))
            return run("cleanup", "running")

        async def cleanup(settings, r):
            ran.append(r)
            return {}

        monkeypatch.setattr(module.maintenance_repo, "begin", begin)
        monkeypatch.setattr(module.maintenance, "run_cleanup", cleanup)
        await w._housekeeping()
        assert begun == [("cleanup", "schedule")] and len(ran) == 1

    async def test_an_idle_worker_still_tidies_up(self, monkeypatch):
        """Regression: the hourly clean-up only ran after a deploy, so a host
        nobody deployed to was never tidied."""
        w, module = self.worker(monkeypatch)
        seen = []

        async def nothing(*a, **kw):
            return None

        async def housekeeping():
            seen.append("housekeeping")

        monkeypatch.setattr(w, "_reclaim", nothing, raising=False)
        monkeypatch.setattr(w, "_idle", nothing, raising=False)
        monkeypatch.setattr(w, "_housekeeping", housekeeping, raising=False)
        monkeypatch.setattr(module.maintenance_repo, "claim", nothing)
        monkeypatch.setattr(module.deployment_repo, "claim_next", nothing)
        await w._deploy_once()
        assert seen == ["housekeeping"]

    async def test_a_clean_up_asked_for_runs_between_deploys(self, monkeypatch):
        w, module = self.worker(monkeypatch)
        order = []

        async def nothing(*a, **kw):
            return None

        async def claim(kind, *, worker):
            order.append(("claim", kind))
            return run("cleanup", "running")

        async def cleanup(r):
            order.append("cleanup")

        async def claim_next(worker_id):
            order.append("deploy")

        monkeypatch.setattr(w, "_reclaim", nothing, raising=False)
        monkeypatch.setattr(w, "_idle", nothing, raising=False)
        monkeypatch.setattr(w, "_housekeeping", nothing, raising=False)
        monkeypatch.setattr(w, "_cleanup", cleanup, raising=False)
        monkeypatch.setattr(module.maintenance_repo, "claim", claim)
        monkeypatch.setattr(module.deployment_repo, "claim_next", claim_next)
        await w._deploy_once()
        assert order == [("claim", "cleanup"), "cleanup", "deploy"]
