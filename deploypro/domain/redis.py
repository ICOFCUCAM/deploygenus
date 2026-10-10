"""A project's built-in Redis: the rules, as pure functions.

Valkey 8 — the engine Render's Key Value runs, open source, and the Redis
protocol, so BullMQ, Celery, RQ, Sidekiq and ioredis use it unchanged. One per
project, on DeployPro's network only: no router labels and no published port,
so nothing outside the host can reach it. docs/design/plan-replace-render.md §3.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from deploypro.domain.errors import InvalidRequest

DEFAULT_IMAGE = "valkey/valkey:8-alpine"
DEFAULT_MEMORY_MB = 256
MIN_MEMORY_MB = 64
MAX_MEMORY_MB = 4096
DEFAULT_ENV_NAME = "REDIS_URL"
PORT = 6379

#: What Valkey does when it is full, in the words the page uses.
POLICIES = {
    "noeviction": "Never evict — refuse new writes when full (for job queues)",
    "allkeys-lru": "Evict the least recently used key (for a cache)",
    "volatile-lru": "Evict the least recently used key that has an expiry",
}
DEFAULT_POLICY = "noeviction"

#: Valkey's own limit, as a share of the container's. The gap is for Valkey's
#: overhead and the AOF rewrite: at 100% the kernel kills the container before
#: Valkey gets to refuse a write.
MAXMEMORY_SHARE = 0.9

#: Above this share of maxmemory the monitor warns: with noeviction, a full
#: Redis starts refusing new jobs.
WARN_PERCENT = 90

#: The data volume's key. `docker_volume_name(slug, "_redis")` gives
#: `deploypro_<slug>__redis`; a user volume name cannot start with an
#: underscore, so no volume a project adds can ever collide with it.
VOLUME_KEY = "_redis"

#: The label carrying a digest of the settings the container was started
#: with. A container whose digest differs from the row's is recreated.
CONFIG_LABEL = "deploypro.redis.config"

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class RedisConfig:
    project_id: UUID
    memory_mb: int
    policy: str
    env_names: tuple[str, ...]
    password_encrypted: bytes
    image: str
    created_at: datetime
    updated_at: datetime


def container_name(project_slug: str) -> str:
    """`deploypro-<slug>-redis`. Web containers end in an 8-hex deployment
    id and workers in a replica number, so neither can take this name."""
    return f"deploypro-{project_slug}-redis"


def validate_memory(value: int | str) -> int:
    try:
        mb = int(str(value).strip())
    except ValueError:
        raise InvalidRequest("Memory must be a whole number of megabytes.") from None
    if not MIN_MEMORY_MB <= mb <= MAX_MEMORY_MB:
        raise InvalidRequest(
            f"Memory must be between {MIN_MEMORY_MB} and {MAX_MEMORY_MB} MB."
        )
    return mb


def validate_policy(value: str) -> str:
    policy = (value or "").strip()
    if policy not in POLICIES:
        raise InvalidRequest(f"Unknown eviction policy {policy!r}.")
    return policy


def parse_env_names(text: str) -> tuple[str, ...]:
    """`REDIS_URL CELERY_BROKER_URL` (spaces, commas or lines) -> both names.

    Upper case, like every variable an app reads, and at least one: a Redis
    the app is never told about is a Redis nobody uses.
    """
    names: list[str] = []
    for raw in re.split(r"[\s,]+", (text or "").strip()):
        if not raw:
            continue
        name = raw.upper()
        if not _ENV_NAME.match(name):
            raise InvalidRequest(
                f"{raw!r} is not a variable name: letters, digits and _, "
                "not starting with a digit."
            )
        if name not in names:
            names.append(name)
    if not names:
        raise InvalidRequest("Give at least one variable name, such as REDIS_URL.")
    if len(names) > 5:
        raise InvalidRequest("At most 5 variable names.")
    return tuple(names)


def url(project_slug: str, password: str) -> str:
    """What the app connects to. `default` is Valkey's built-in user, which
    `--requirepass` protects; database 0."""
    return f"redis://default:{password}@{container_name(project_slug)}:{PORT}/0"


def variables(project_slug: str, config: RedisConfig, password: str) -> dict[str, str]:
    value = url(project_slug, password)
    return {name: value for name in config.env_names}


def maxmemory_mb(memory_mb: int) -> int:
    return max(1, int(memory_mb * MAXMEMORY_SHARE))


def server_command(memory_mb: int, policy: str) -> str:
    """The container's command, run by `sh -c`.

    The password comes from the container's environment, not the command
    line: arguments are visible to every user of the host in `ps`, and the
    environment is not. `exec` hands PID 1 to valkey-server, so a stop
    signal reaches it and it writes the AOF out before exiting.
    """
    return (
        'exec docker-entrypoint.sh valkey-server --requirepass "$VALKEY_PASSWORD" '
        f"--maxmemory {maxmemory_mb(memory_mb)}mb "
        f"--maxmemory-policy {validate_policy(policy)} "
        "--appendonly yes --appendfsync everysec --aof-load-truncated yes "
        "--dir /data --save ''"
    )


def config_digest(image: str, memory_mb: int, policy: str) -> str:
    """A short digest of everything the container is started with, kept on it
    as a label. The password is left out on purpose — a label is readable by
    anyone who can run `docker inspect` — and never changes anyway."""
    text = f"{image}|{memory_mb}|{policy}"
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def used_percent(used_bytes: int | None, memory_mb: int) -> int | None:
    """Memory used as a share of Valkey's maxmemory."""
    if used_bytes is None:
        return None
    limit = maxmemory_mb(memory_mb) * 1024 * 1024
    return round(used_bytes * 100 / limit) if limit else None


def parse_info(text: str) -> dict[str, str]:
    """`INFO` output -> {field: value}. Sections and blank lines are skipped."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key] = value
    return fields


def key_count(info: dict[str, str]) -> int:
    """Keys across every database, from the `# Keyspace` lines
    (`db0:keys=12,expires=0,avg_ttl=0`)."""
    total = 0
    for key, value in info.items():
        if key.startswith("db") and key[2:].isdigit():
            for part in value.split(","):
                name, _, number = part.partition("=")
                if name == "keys" and number.isdigit():
                    total += int(number)
    return total
