"""A project's built-in Redis: starting it, keeping it running, removing it.

Not tied to deploys. A deploy, promotion or rollback never touches the Redis
container, so queued jobs survive all three — the reason to have one. The
monitor's minute-by-minute pass calls `ensure_running`, which starts a missing
or stopped container and recreates one whose settings changed, always on the
same volume. docs/design/plan-replace-render.md §3.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass

from deploypro.adapters import containers, crypto
from deploypro.config import Settings
from deploypro.domain import redis as rules
from deploypro.domain.errors import Conflict, NotFound
from deploypro.domain.models import EnvTarget, Project
from deploypro.domain.redis import RedisConfig
from deploypro.domain.storage import Mount, docker_volume_name
from deploypro.repositories import redis as redis_repo

ROLE = "redis"

#: How long a stop gives Valkey to write its AOF out. Seconds are plenty.
STOP_TIMEOUT = 30


@dataclass(frozen=True, slots=True)
class Status:
    """What the Redis page and the monitor show."""

    state: str  # running, restarting, exited, … or "missing"
    used_bytes: int | None = None
    used_percent: int | None = None
    keys: int | None = None
    error: str | None = None

    @property
    def running(self) -> bool:
        return self.state == "running"


def volume_name(project: Project) -> str:
    return docker_volume_name(project.slug, rules.VOLUME_KEY)


async def enable(
    project: Project,
    *,
    memory_mb: int,
    policy: str,
    env_names: tuple[str, ...],
    settings: Settings,
) -> RedisConfig:
    """Record the settings with a new password, then start the container.

    A volume kept from an earlier Redis of this project is reused, so adding
    Redis back brings its keys back. The password is new either way; Valkey
    keeps no password in its data.
    """
    password = secrets.token_hex(24)
    config = await redis_repo.enable(
        project_id=project.id,
        memory_mb=rules.validate_memory(memory_mb),
        policy=rules.validate_policy(policy),
        env_names=env_names,
        password_encrypted=crypto.encrypt(password, key=settings.master_key),
        image=settings.redis_image,
    )
    await ensure_running(project, config, settings=settings)
    return config


async def update(project: Project, changes: dict, *, settings: Settings) -> RedisConfig:
    """Change memory, policy or variable names. Memory and policy recreate the
    container on the same volume; a variable name only takes effect for the
    app at its next deploy, because the app reads its environment at start."""
    config = await redis_repo.update(project.id, changes)
    await ensure_running(project, config, settings=settings)
    return config


async def restart(project: Project, *, settings: Settings) -> None:
    config = await _require(project)
    await _start(project, config, settings=settings)


async def disable(project: Project, *, delete_data: bool) -> bool:
    """Stop and remove the container and forget the settings. The data volume
    goes only when the owner ticked that it should; otherwise it is kept and
    reused if Redis is added again. Returns whether data was deleted."""
    await _require(project)
    await containers.remove_by_name(rules.container_name(project.slug))
    await redis_repo.disable(project.id)
    if delete_data:
        return await containers.remove_volume(volume_name(project))
    return False


async def ensure_running(
    project: Project, config: RedisConfig, *, settings: Settings
) -> str:
    """Make the container match `config`. Returns what was done: `ok`,
    `started` (it was stopped) or `created` (it was missing or out of date)."""
    name = rules.container_name(project.slug)
    state = await containers.state(name)
    wanted = rules.config_digest(config.image, config.memory_mb, config.policy)
    if state is not None and await containers.label(name, rules.CONFIG_LABEL) == wanted:
        if state == "running" or state == "restarting":
            return "ok"
        await containers.start(name)
        return "started"
    await _start(project, config, settings=settings)
    return "created"


async def status(project: Project, config: RedisConfig | None = None) -> Status:
    config = config or await redis_repo.get(project.id)
    if config is None:
        raise NotFound("This project has no Redis.")
    name = rules.container_name(project.slug)
    state = await containers.state(name)
    if state != "running":
        return Status(state=state or "missing")
    try:
        info = rules.parse_info(
            await _cli(name, "INFO memory") + "\n" + await _cli(name, "INFO keyspace")
        )
    except containers.DockerError:
        return Status(state=state, error="it is running but did not answer INFO")
    used = int(info["used_memory"]) if info.get("used_memory", "").isdigit() else None
    return Status(
        state=state,
        used_bytes=used,
        used_percent=rules.used_percent(used, config.memory_mb),
        keys=rules.key_count(info),
    )


async def ping(project: Project) -> str | None:
    """None when Redis answers PONG; otherwise what is wrong, in words. Never
    carries the password: the problem text ends up in an alert."""
    name = rules.container_name(project.slug)
    state = await containers.state(name)
    if state != "running":
        return f"its container is {state or 'missing'}"
    try:
        answer = (await _cli(name, "PING")).strip()
    except containers.DockerError:
        return "it did not answer PING"
    return None if answer == "PONG" else f"PING answered {answer[:40]!r}"


async def rewrite_aof(project: Project, *, timeout: float = 120) -> bool:
    """Compact the append-only file before a backup copies it, and wait for
    the rewrite to finish. False if Redis is not running (the volume is then
    copied as it lies, which Valkey loads anyway)."""
    name = rules.container_name(project.slug)
    if await containers.state(name) != "running":
        return False
    await _cli(name, "BGREWRITEAOF")
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.5)
        info = rules.parse_info(await _cli(name, "INFO persistence"))
        if (
            info.get("aof_rewrite_in_progress") == "0"
            and info.get("aof_rewrite_scheduled") == "0"
        ):
            return True
    return False


async def variables(
    project: Project, *, target: EnvTarget, settings: Settings
) -> dict[str, str]:
    """`{REDIS_URL: redis://…}` for production, nothing for a preview: a
    preview build must not consume production's queue (R4)."""
    if target is not EnvTarget.PRODUCTION:
        return {}
    config = await redis_repo.get(project.id)
    if config is None:
        return {}
    return rules.variables(project.slug, config, password(config, settings=settings))


def password(config: RedisConfig, *, settings: Settings) -> str:
    return crypto.decrypt(config.password_encrypted, key=settings.master_key)


async def _require(project: Project) -> RedisConfig:
    config = await redis_repo.get(project.id)
    if config is None:
        raise NotFound("This project has no Redis.")
    return config


async def _start(project: Project, config: RedisConfig, *, settings: Settings) -> str:
    name = volume_name(project)
    await containers.ensure_volume(
        name,
        labels={
            containers.OWNER_LABEL: containers.OWNER_VALUE,
            containers.PROJECT_LABEL: project.slug,
            "deploypro.volume": rules.VOLUME_KEY,
        },
    )
    try:
        await containers.ensure_image(config.image)
    except containers.DockerError as exc:
        raise Conflict(f"Could not pull {config.image}: {exc}") from exc
    return await containers.run_service(
        containers.ServiceSpec(
            image=config.image,
            name=rules.container_name(project.slug),
            network=settings.network,
            memory_mb=config.memory_mb,
            shell_command=rules.server_command(config.memory_mb, config.policy),
            env={"VALKEY_PASSWORD": password(config, settings=settings)},
            labels={
                containers.OWNER_LABEL: containers.OWNER_VALUE,
                containers.PROJECT_LABEL: project.slug,
                containers.ROLE_LABEL: ROLE,
                rules.CONFIG_LABEL: rules.config_digest(
                    config.image, config.memory_mb, config.policy
                ),
            },
            volumes=(Mount(volume=name, path="/data"),),
            stop_timeout=STOP_TIMEOUT,
        )
    )


async def _cli(name: str, command: str) -> str:
    """valkey-cli inside the container, authenticated from the container's
    own environment so the password is never an argument anywhere."""
    return await containers.exec_capture(
        name, f'REDISCLI_AUTH="$VALKEY_PASSWORD" valkey-cli --no-auth-warning {command}'
    )
