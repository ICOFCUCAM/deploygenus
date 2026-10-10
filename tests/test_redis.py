"""A project's built-in Redis. docs/design/plan-replace-render.md §3.

The real-Docker checks (§3.9) were run separately; these pin the rules, the
container's arguments, the engine's decisions, the monitor and the page.
"""

# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from cryptography.fernet import Fernet

from deploypro.adapters import containers, crypto
from deploypro.domain import redis as rules
from deploypro.domain.errors import InvalidRequest
from deploypro.domain.models import EnvTarget
from deploypro.engine import monitor as monitor_module
from deploypro.engine import redis as engine
from tests import fakes
from tests.test_dashboard import anon, app, client, repos  # noqa: F401

KEY = Fernet.generate_key().decode()
PASSWORD = "a" * 48
SETTINGS = SimpleNamespace(
    network="deploypro", master_key=KEY, redis_image=rules.DEFAULT_IMAGE
)
NOW = datetime(2026, 10, 10, tzinfo=UTC)


def config(**kw):
    base = dict(
        project_id=fakes.project().id,
        memory_mb=256,
        policy="noeviction",
        env_names=("REDIS_URL",),
        password_encrypted=crypto.encrypt(PASSWORD, key=KEY),
        image=rules.DEFAULT_IMAGE,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(kw)
    return rules.RedisConfig(**base)


class TestRules:
    def test_the_url_names_the_container_on_the_network(self):
        assert (
            rules.url("cineforge", "pw")
            == "redis://default:pw@deploypro-cineforge-redis:6379/0"
        )

    def test_every_variable_name_gets_the_url(self):
        got = rules.variables(
            "cf", config(env_names=("REDIS_URL", "CELERY_BROKER_URL")), "pw"
        )
        assert set(got) == {"REDIS_URL", "CELERY_BROKER_URL"}
        assert len(set(got.values())) == 1

    @pytest.mark.parametrize(
        ("text", "names"),
        [
            ("REDIS_URL", ("REDIS_URL",)),
            (
                " redis_url, CELERY_BROKER_URL\nREDIS_URL ",
                ("REDIS_URL", "CELERY_BROKER_URL"),
            ),
        ],
    )
    def test_names_are_tidied(self, text, names):
        assert rules.parse_env_names(text) == names

    @pytest.mark.parametrize("text", ["", "1REDIS", "REDIS-URL", "A B C D E F"])
    def test_bad_names_are_refused(self, text):
        with pytest.raises(InvalidRequest):
            rules.parse_env_names(text)

    @pytest.mark.parametrize("value", ["63", "4097", "lots"])
    def test_memory_outside_the_bounds_is_refused(self, value):
        with pytest.raises(InvalidRequest):
            rules.validate_memory(value)

    def test_an_unknown_policy_is_refused(self):
        with pytest.raises(InvalidRequest):
            rules.validate_policy("allkeys-random")

    def test_the_command_reads_the_password_from_the_environment(self):
        command = rules.server_command(256, "noeviction")
        assert '"$VALKEY_PASSWORD"' in command and PASSWORD not in command
        assert "--maxmemory 230mb" in command  # 90% of 256
        assert "--maxmemory-policy noeviction" in command
        assert "--appendonly yes --appendfsync everysec" in command

    def test_the_digest_follows_the_settings_and_never_the_password(self):
        a = rules.config_digest("img", 256, "noeviction")
        assert a == rules.config_digest("img", 256, "noeviction")
        assert a != rules.config_digest("img", 512, "noeviction")
        assert a != rules.config_digest("img", 256, "allkeys-lru")

    def test_info_and_keys(self):
        info = rules.parse_info(
            "# Memory\r\nused_memory:1048576\r\n\r\n# Keyspace\r\n"
            "db0:keys=12,expires=0,avg_ttl=0\r\ndb3:keys=3,expires=1,avg_ttl=9\r\n"
        )
        assert info["used_memory"] == "1048576"
        assert rules.key_count(info) == 15
        assert rules.used_percent(115 * 1048576, 128) == 100

    def test_the_volume_cannot_collide_with_a_project_volume(self):
        from deploypro.domain.storage import docker_volume_name, validate_volume_name

        assert docker_volume_name("cf", rules.VOLUME_KEY) == "deploypro_cf__redis"
        with pytest.raises(InvalidRequest):
            validate_volume_name(rules.VOLUME_KEY)


@pytest.fixture
def docker(monkeypatch):
    """The Docker calls the engine makes, scripted."""
    seen = SimpleNamespace(
        state=None, label=None, started=[], ran=[], removed=[], volumes=[]
    )

    async def state(name):
        return seen.state

    async def label(name, key):
        return seen.label

    async def start(name):
        seen.started.append(name)

    async def run_service(spec):
        seen.ran.append(spec)
        return "cid"

    async def nothing(*a, **k):
        return None

    async def remove_by_name(name):
        seen.removed.append(name)

    async def remove_volume(name):
        seen.volumes.append(name)
        return True

    for name, fn in {
        "state": state,
        "label": label,
        "start": start,
        "run_service": run_service,
        "ensure_volume": nothing,
        "ensure_image": nothing,
        "remove_by_name": remove_by_name,
        "remove_volume": remove_volume,
    }.items():
        monkeypatch.setattr(containers, name, fn)
    return seen


class TestEngine:
    async def test_a_matching_running_container_is_left_alone(self, docker):
        docker.state = "running"
        docker.label = rules.config_digest(rules.DEFAULT_IMAGE, 256, "noeviction")
        assert (
            await engine.ensure_running(fakes.project(), config(), settings=SETTINGS)
            == "ok"
        )
        assert docker.ran == [] and docker.started == []

    async def test_a_stopped_one_is_started_not_recreated(self, docker):
        docker.state = "exited"
        docker.label = rules.config_digest(rules.DEFAULT_IMAGE, 256, "noeviction")
        assert (
            await engine.ensure_running(fakes.project(), config(), settings=SETTINGS)
            == "started"
        )
        assert docker.started == ["deploypro-blog-redis"] and docker.ran == []

    @pytest.mark.parametrize("state", [None, "running"])
    async def test_a_missing_or_changed_one_is_created_on_the_same_volume(
        self, docker, state
    ):
        docker.state, docker.label = state, "old-digest"
        assert (
            await engine.ensure_running(fakes.project(), config(), settings=SETTINGS)
            == "created"
        )
        spec = docker.ran[0]
        assert spec.name == "deploypro-blog-redis" and spec.memory_mb == 256
        assert (
            spec.volumes[0].volume == "deploypro_blog__redis"
            and spec.volumes[0].path == "/data"
        )
        assert spec.env == {"VALKEY_PASSWORD": PASSWORD}
        assert PASSWORD not in spec.shell_command

    async def test_production_gets_the_url_and_a_preview_nothing(self, monkeypatch):
        async def get(_id):
            return config(env_names=("REDIS_URL", "CELERY_BROKER_URL"))

        monkeypatch.setattr("deploypro.repositories.redis.get", get)
        prod = await engine.variables(
            fakes.project(), target=EnvTarget.PRODUCTION, settings=SETTINGS
        )
        assert (
            prod["REDIS_URL"] == rules.url("blog", PASSWORD) == prod["CELERY_BROKER_URL"]
        )
        assert (
            await engine.variables(
                fakes.project(), target=EnvTarget.PREVIEW, settings=SETTINGS
            )
            == {}
        )

    async def test_without_redis_there_are_no_variables(self):
        assert (
            await engine.variables(
                fakes.project(), target=EnvTarget.PRODUCTION, settings=SETTINGS
            )
            == {}
        )

    @pytest.mark.parametrize(
        ("delete", "volumes"), [(False, []), (True, ["deploypro_blog__redis"])]
    )
    async def test_removing_keeps_the_data_unless_asked(
        self, docker, monkeypatch, delete, volumes
    ):
        async def get(_id):
            return config()

        async def disable(_id):
            pass

        monkeypatch.setattr("deploypro.repositories.redis.get", get)
        monkeypatch.setattr("deploypro.repositories.redis.disable", disable)
        await engine.disable(fakes.project(), delete_data=delete)
        assert docker.removed == ["deploypro-blog-redis"] and docker.volumes == volumes


class TestContainer:
    async def test_no_port_no_route_and_the_password_never_an_argument(
        self, monkeypatch, tmp_path
    ):
        seen = {}

        async def capture(args, timeout=120):
            seen["args"] = args
            env_file = args[args.index("--env-file") + 1]
            seen["env_file"] = Path(env_file).read_text()
            return "cid\n"

        async def nothing(name):
            return None

        monkeypatch.setattr(containers, "_capture", capture)
        monkeypatch.setattr(containers, "remove_by_name", nothing)
        await containers.run_service(
            containers.ServiceSpec(
                image=rules.DEFAULT_IMAGE,
                name="deploypro-cf-redis",
                network="deploypro",
                memory_mb=256,
                shell_command=rules.server_command(256, "noeviction"),
                env={"VALKEY_PASSWORD": PASSWORD},
                labels={"deploypro.role": "redis"},
            )
        )
        args = seen["args"]
        assert not any(a in ("-p", "--publish", "-P", "--publish-all") for a in args)
        assert "traefik.enable=false" in args and "--network" in args
        assert PASSWORD not in " ".join(args)
        assert seen["env_file"] == f"VALKEY_PASSWORD={PASSWORD}\n"
        assert (
            "--restart" in args and args[args.index("--restart") + 1] == "unless-stopped"
        )


class TestMonitor:
    @pytest.fixture
    def world(self, monkeypatch):
        state = SimpleNamespace(ping=None, percent=10, fired=[], resolved=[])

        async def list_all():
            return [config()]

        async def get(_id):
            return fakes.project()

        async def ensure(*a, **k):
            return "ok"

        async def ping(project):
            return state.ping

        async def status(project, cfg):
            return engine.Status(
                state="running", used_bytes=1, used_percent=state.percent, keys=1
            )

        monkeypatch.setattr("deploypro.repositories.redis.list_all", list_all)
        monkeypatch.setattr(monitor_module.project_repo, "get", get)
        monkeypatch.setattr(monitor_module.redis_engine, "ensure_running", ensure)
        monkeypatch.setattr(monitor_module.redis_engine, "ping", ping)
        monkeypatch.setattr(monitor_module.redis_engine, "status", status)

        class Alerts:
            async def fire(self, key, **kw):
                state.fired.append((key, kw["title"]))

            async def resolve(self, key, **kw):
                state.resolved.append(key)

        state.monitor = monitor_module.Monitor(SETTINGS, Alerts())
        return state

    async def test_a_redis_that_does_not_answer_alerts_on_the_second_check(self, world):
        world.ping = "its container is exited"
        await world.monitor._check_redis()
        assert world.fired == []
        await world.monitor._check_redis()
        assert world.fired == [("redis:blog", "Blog: Redis is down")]

    async def test_nearly_full_warns_and_resolves_with_a_margin(self, world):
        world.percent = 92
        await world.monitor._check_redis()
        assert ("redis-memory:blog", "Blog: Redis 92% full") in world.fired
        world.percent = 88
        await world.monitor._check_redis()
        assert "redis-memory:blog" not in world.resolved  # within the margin
        world.percent = 50
        await world.monitor._check_redis()
        assert "redis-memory:blog" in world.resolved


class TestPage:
    async def test_off_offers_to_add_it(self, client, repos, monkeypatch):
        async def missing(_name):
            return False

        monkeypatch.setattr(containers, "volume_exists", missing)
        body = (await client.get("/projects/blog/config/redis")).text
        assert (
            "Add Redis" in body and 'value="REDIS_URL"' in body and "Never evict" in body
        )

    @pytest.fixture
    def on(self, monkeypatch, repos):
        async def get(_id):
            return config()

        async def status(project, cfg):
            return engine.Status(
                state="running", used_bytes=2 * 1048576, used_percent=1, keys=7
            )

        monkeypatch.setattr("deploypro.web.redis.redis_repo.get", get)
        monkeypatch.setattr("deploypro.web.redis.redis_engine.status", status)
        monkeypatch.setattr("deploypro.web.pages.redis_repo.get", get)

    async def test_on_shows_its_state_and_never_the_password(self, client, repos, on):
        body = (await client.get("/projects/blog/config/redis")).text
        assert "Running" in body and "7" in body and "Remove Redis" in body
        assert PASSWORD not in body

    async def test_the_url_is_shown_only_when_asked_and_not_cached(
        self, client, repos, on, monkeypatch
    ):
        monkeypatch.setattr(
            "deploypro.web.redis.redis_engine.password", lambda cfg, settings: PASSWORD
        )
        response = await client.post("/projects/blog/redis/url")
        assert rules.url("blog", PASSWORD) in response.text
        assert response.headers["cache-control"] == "no-store"

    async def test_adding_it_redirects_with_what_happens_next(
        self, client, repos, monkeypatch
    ):
        seen = {}

        async def enable(project, **kw):
            seen.update(kw)
            return config(env_names=kw["env_names"])

        monkeypatch.setattr("deploypro.web.redis.redis_engine.enable", enable)
        response = await client.post(
            "/projects/blog/redis",
            data={"memory_mb": "512", "policy": "noeviction", "env_names": "REDIS_URL"},
        )
        assert "Redis is running" in unquote(response.headers["location"])
        assert seen["memory_mb"] == 512 and seen["env_names"] == ("REDIS_URL",)

    async def test_a_bad_size_is_refused(self, client, repos):
        response = await client.post("/projects/blog/redis", data={"memory_mb": "9"})
        assert "err=" in response.headers["location"]

    @pytest.mark.parametrize(
        ("ticked", "words"),
        [({}, "data is kept"), ({"delete_data": "1"}, "data deleted")],
    )
    async def test_removing(self, client, repos, monkeypatch, ticked, words):
        async def disable(project, *, delete_data):
            return delete_data

        monkeypatch.setattr("deploypro.web.redis.redis_engine.disable", disable)
        response = await client.post("/projects/blog/redis/delete", data=ticked)
        assert words in unquote(response.headers["location"])


class TestEnvironment:
    async def test_workers_and_jobs_get_it_in_production(self, monkeypatch):
        from deploypro.engine import processes

        seen = {}

        async def get(_id):
            return config()

        async def collect(project_id, *, target, key, injected):
            seen.update(injected)

        monkeypatch.setattr("deploypro.repositories.redis.get", get)
        monkeypatch.setattr(processes.environment, "collect", collect)
        settings = SimpleNamespace(
            **vars(SETTINGS), deployment_url=lambda s: f"https://{s}"
        )
        await processes._environment(
            fakes.project(),
            fakes.deployment(),
            settings=settings,
            target=EnvTarget.PRODUCTION,
        )
        assert seen["REDIS_URL"] == rules.url("blog", PASSWORD)
        assert seen["DEPLOYPRO_ENV"] == "production"

    async def test_a_value_the_owner_set_wins(self, monkeypatch):
        from deploypro.engine import environment

        async def list_env(_id):
            from dataclasses import replace

            var = fakes.env_var("REDIS_URL")
            return [
                replace(
                    var, value_encrypted=crypto.encrypt("redis://elsewhere:6379", key=KEY)
                )
            ]

        monkeypatch.setattr(environment.project_repo, "list_env", list_env)
        env = await environment.collect(
            fakes.project().id,
            target=EnvTarget.PRODUCTION,
            key=KEY,
            injected={"REDIS_URL": rules.url("blog", PASSWORD)},
        )
        assert env.all["REDIS_URL"] == "redis://elsewhere:6379"
