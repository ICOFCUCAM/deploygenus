"""A project's Redis — the configuration, not the data.

The data is in the Docker volume `deploypro_<slug>__redis`, which this module
never touches: removing the row stops the container being kept running, and
the engine decides separately whether the volume goes too.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from deploypro.adapters import db
from deploypro.domain.errors import Conflict, NotFound
from deploypro.domain.redis import RedisConfig

COLUMNS = (
    "project_id, memory_mb, policy, env_names, password_encrypted, image, "
    "created_at, updated_at"
)

#: Settings the page may change. The password and image are set at enable.
UPDATABLE = frozenset({"memory_mb", "policy", "env_names", "image"})


def to_config(row: dict[str, Any]) -> RedisConfig:
    return RedisConfig(
        project_id=row["project_id"],
        memory_mb=row["memory_mb"],
        policy=row["policy"],
        env_names=tuple(row["env_names"].split()),
        password_encrypted=bytes(row["password_encrypted"]),
        image=row["image"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def get(project_id: UUID) -> RedisConfig | None:
    async with db.connection() as conn:
        cur = await conn.execute(
            f"SELECT {COLUMNS} FROM project_redis WHERE project_id = %s", (project_id,)
        )
        row = await cur.fetchone()
    return to_config(row) if row else None


async def list_all() -> list[RedisConfig]:
    async with db.connection() as conn:
        cur = await conn.execute(f"SELECT {COLUMNS} FROM project_redis")
        rows = await cur.fetchall()
    return [to_config(row) for row in rows]


async def enable(
    *,
    project_id: UUID,
    memory_mb: int,
    policy: str,
    env_names: tuple[str, ...],
    password_encrypted: bytes,
    image: str,
) -> RedisConfig:
    async with db.connection() as conn:
        try:
            cur = await conn.execute(
                f"""
                INSERT INTO project_redis
                    (project_id, memory_mb, policy, env_names, password_encrypted, image)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING {COLUMNS}
                """,
                (
                    project_id,
                    memory_mb,
                    policy,
                    " ".join(env_names),
                    password_encrypted,
                    image,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - narrowed immediately below
            if "project_redis_pkey" in str(exc):
                raise Conflict("This project already has Redis.") from exc
            raise
        row = await cur.fetchone()
    return to_config(row)


async def update(project_id: UUID, changes: dict[str, Any]) -> RedisConfig:
    unknown = set(changes) - UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    values = dict(changes)
    if "env_names" in values:
        values["env_names"] = " ".join(values["env_names"])
    assignments = ", ".join(f"{column} = %s" for column in values)
    async with db.connection() as conn:
        cur = await conn.execute(
            f"UPDATE project_redis SET {assignments}, updated_at = now() "
            f"WHERE project_id = %s RETURNING {COLUMNS}",
            (*values.values(), project_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFound("This project has no Redis.")
    return to_config(row)


async def disable(project_id: UUID) -> None:
    async with db.connection() as conn:
        await conn.execute(
            "DELETE FROM project_redis WHERE project_id = %s", (project_id,)
        )
