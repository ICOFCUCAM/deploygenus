"""Alerts: sent once when something breaks, once when it recovers."""

from __future__ import annotations

import pytest

from deploypro.adapters import notify
from deploypro.engine import monitor as monitor_module
from deploypro.engine.alerts import Alerts
from deploypro.engine.monitor import Monitor
from tests import fakes


@pytest.fixture
def sent(monkeypatch):
    messages: list[tuple[str, str]] = []

    async def send(url, *, title, detail, level, **fields):
        messages.append((level, title))
        return True

    monkeypatch.setattr(notify, "send", send)
    return messages


class TestAlerts:
    async def test_a_condition_fires_once_however_often_it_is_seen(self, sent):
        alerts = Alerts("https://hooks.example.com/x")
        for _ in range(5):
            await alerts.fire("site:blog", title="Blog is down")
        assert sent == [("critical", "Blog is down")]

    async def test_it_resolves_once_and_only_if_it_fired(self, sent):
        alerts = Alerts("https://hooks.example.com/x")
        await alerts.resolve("site:blog", title="Blog is back")
        await alerts.fire("site:blog", title="Blog is down")
        await alerts.resolve("site:blog", title="Blog is back")
        await alerts.resolve("site:blog", title="Blog is back")
        assert sent == [("critical", "Blog is down"), ("resolved", "Blog is back")]

    async def test_it_can_fire_again_after_resolving(self, sent):
        alerts = Alerts("https://hooks.example.com/x")
        await alerts.fire("k", title="down")
        await alerts.resolve("k", title="up")
        await alerts.fire("k", title="down")
        assert [level for level, _ in sent] == ["critical", "resolved", "critical"]


class TestNotify:
    async def test_no_url_sends_nothing(self):
        assert await notify.send("", title="t", detail="d", level="warning") is False

    async def test_an_unreachable_webhook_never_raises(self):
        # Port 9 (discard) is closed on any sane host: the connection fails.
        delivered = await notify.send(
            "http://127.0.0.1:9/hook", title="t", detail="d", level="critical"
        )
        assert delivered is False

    def test_the_message_fits_in_a_discord_message(self):
        message = notify.render(title="t", detail="x" * 5000, level="critical")
        assert len(message) <= 2000

    def test_the_level_is_visible_at_a_glance(self):
        assert notify.render(title="Blog is down", detail="", level="critical") == (
            "🔴 DeployPro: Blog is down"
        )


class FakeSettings:
    network = "deploypro"
    health_path = "/"
    disk_alert_percent = 90
    build_root = "/nonexistent"


@pytest.fixture
def world(monkeypatch):
    """One project with a live production deployment and one worker."""
    live = fakes.deployment()
    state = {
        "project": fakes.project(production_deployment_id=live.id),
        "deployment": live,
        "probe": None,
        "worker_state": "running",
        "workers": [fakes.process()],
    }

    async def list_all():
        return [state["project"]]

    async def get(_id):
        return state["deployment"]

    async def probe(**_):
        return state["probe"]

    async def worker_state(_name):
        return state["worker_state"]

    async def long_running(_id):
        return state["workers"]

    monkeypatch.setattr(monitor_module.project_repo, "list_all", list_all)
    monkeypatch.setattr(monitor_module.deployment_repo, "get", get)
    monkeypatch.setattr(monitor_module.health, "probe", probe)
    monkeypatch.setattr(monitor_module.containers, "state", worker_state)
    monkeypatch.setattr(monitor_module.process_repo, "list_long_running", long_running)
    monkeypatch.setattr(monitor_module, "disk_used_percent", lambda _p: 40)
    return state


class TestMonitor:
    def monitor(self):
        return Monitor(FakeSettings(), Alerts("https://hooks.example.com/x"))

    async def test_one_failed_check_is_not_an_outage(self, world, sent):
        world["probe"] = "it did not answer HTTP: ConnectError"
        m = self.monitor()
        await m.check()
        assert sent == []

    async def test_two_in_a_row_are(self, world, sent):
        world["probe"] = "it did not answer HTTP: ConnectError"
        m = self.monitor()
        await m.check()
        await m.check()
        await m.check()
        assert sent == [("critical", "Blog is down")]

    async def test_a_blip_between_failures_resets_the_count(self, world, sent):
        m = self.monitor()
        world["probe"] = "down"
        await m.check()
        world["probe"] = None
        await m.check()
        world["probe"] = "down"
        await m.check()
        assert sent == []

    async def test_recovery_is_reported(self, world, sent):
        m = self.monitor()
        world["probe"] = "down"
        await m.check()
        await m.check()
        world["probe"] = None
        await m.check()
        assert sent == [
            ("critical", "Blog is down"),
            ("resolved", "Blog is serving again"),
        ]

    async def test_a_crashed_worker_is_reported(self, world, sent):
        m = self.monitor()
        world["worker_state"] = "restarting"
        await m.check()
        await m.check()
        assert sent == [("critical", "Blog: worker mailer is not running")]

    async def test_a_worker_that_was_never_started_is_not_down(self, world, sent):
        """Added since the last promotion; it starts on the next one."""
        m = self.monitor()
        world["worker_state"] = None
        await m.check()
        await m.check()
        assert sent == []

    async def test_a_project_that_never_went_live_is_not_watched(self, world, sent):
        world["project"] = fakes.project(production_deployment_id=None)
        world["probe"] = "down"
        m = self.monitor()
        await m.check()
        await m.check()
        assert sent == []

    async def test_a_full_disk_alerts_and_resolves_with_a_margin(
        self, world, sent, monkeypatch
    ):
        m = self.monitor()
        usage = {"now": 95}
        monkeypatch.setattr(monitor_module, "disk_used_percent", lambda _p: usage["now"])
        await m.check()
        usage["now"] = 88  # below 90, but not by enough to call it over
        await m.check()
        usage["now"] = 80
        await m.check()
        assert sent == [
            ("critical", "Disk 95% full"),
            ("resolved", "Disk back to 80% full"),
        ]
