"""Clean-up and backup runs, and the host facts beside them.

A run is requested by the dashboard (`request`) and claimed by the worker
(`claim`), or begun by the worker itself on its schedule (`begin`). Either
way it is finished (`finish` / `fail`) by the worker. At most one of each kind
is open at a time; the database enforces it.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from deploypro.adapters import db
from deploypro.domain.errors import Conflict
from deploypro.domain.maintenance import STALE_AFTER, MaintenanceRun

COLUMNS = (
    "id, kind, origin, status, requested_at, started_at, finished_at, worker, "
    "summary, detail, error"
)

WORDS = {"cleanup": "clean-up", "backup": "backup"}


def _run(row: dict[str, Any]) -> MaintenanceRun:
    return MaintenanceRun(**row)


def _is_open_conflict(exc: Exception) -> bool:
    return "maintenance_runs_one_open" in str(exc)


async def request(kind: str) -> MaintenanceRun:
    """Ask the worker for a run. Refused while one of the kind is open."""
    async with db.connection() as conn:
        try:
            cur = await conn.execute(
                f"""
                INSERT INTO maintenance_runs (kind, origin, status)
                VALUES (%s, 'dashboard', 'requested')
                RETURNING {COLUMNS}
                """,
                (kind,),
            )
        except Exception as exc:  # noqa: BLE001 - narrowed immediately below
            if _is_open_conflict(exc):
                raise Conflict(f"A {WORDS[kind]} is already running.") from exc
            raise
        return _run(await cur.fetchone())


async def begin(kind: str, *, origin: str, worker: str) -> MaintenanceRun | None:
    """A run the worker starts itself. None when one is already open."""
    async with db.connection() as conn:
        try:
            cur = await conn.execute(
                f"""
                INSERT INTO maintenance_runs
                       (kind, origin, status, started_at, worker)
                VALUES (%s, %s, 'running', now(), %s)
                RETURNING {COLUMNS}
                """,
                (kind, origin, worker),
            )
        except Exception as exc:  # noqa: BLE001 - narrowed immediately below
            if _is_open_conflict(exc):
                return None
            raise
        return _run(await cur.fetchone())


async def claim(kind: str, *, worker: str) -> MaintenanceRun | None:
    """Take the requested run of `kind`, if there is one."""
    async with db.connection() as conn:
        cur = await conn.execute(
            f"""
            UPDATE maintenance_runs
               SET status = 'running', started_at = now(), worker = %s
             WHERE id = (
                   SELECT id FROM maintenance_runs
                    WHERE kind = %s AND status = 'requested'
                      FOR UPDATE SKIP LOCKED
                    LIMIT 1
             )
            RETURNING {COLUMNS}
            """,
            (worker, kind),
        )
        row = await cur.fetchone()
    return _run(row) if row else None


async def finish(run_id: int, *, summary: str, detail: dict[str, Any]) -> None:
    async with db.connection() as conn:
        await conn.execute(
            """
            UPDATE maintenance_runs
               SET status = 'succeeded', finished_at = now(), summary = %s, detail = %s
             WHERE id = %s
            """,
            (summary, Jsonb(detail), run_id),
        )


async def fail(run_id: int, *, error: str) -> None:
    async with db.connection() as conn:
        await conn.execute(
            """
            UPDATE maintenance_runs
               SET status = 'failed', finished_at = now(), error = %s
             WHERE id = %s
            """,
            (error[:2000], run_id),
        )


async def abandon_stale() -> int:
    """Fail runs whose worker stopped under them, so they do not block the
    next one of their kind for ever."""
    total = 0
    async with db.connection() as conn:
        for kind, limit in STALE_AFTER.items():
            cur = await conn.execute(
                """
                UPDATE maintenance_runs
                   SET status = 'failed', finished_at = now(),
                       error = 'The worker stopped before it finished.'
                 WHERE kind = %s AND status = 'running' AND started_at < now() - %s
                """,
                (kind, limit),
            )
            total += cur.rowcount or 0
    return total


async def latest(kind: str) -> tuple[MaintenanceRun | None, MaintenanceRun | None]:
    """(the open run, the last finished run) of `kind`; either may be None."""
    async with db.connection() as conn:
        cur = await conn.execute(
            f"""
            SELECT {COLUMNS} FROM maintenance_runs
             WHERE kind = %s AND status IN ('requested', 'running')
             LIMIT 1
            """,
            (kind,),
        )
        open_row = await cur.fetchone()
        cur = await conn.execute(
            f"""
            SELECT {COLUMNS} FROM maintenance_runs
             WHERE kind = %s AND status IN ('succeeded', 'failed')
             ORDER BY id DESC
             LIMIT 1
            """,
            (kind,),
        )
        done_row = await cur.fetchone()
    return (
        _run(open_row) if open_row else None,
        _run(done_row) if done_row else None,
    )


async def delete_old(*, keep: int = 200) -> int:
    """Runs are a history, not an archive: an hourly clean-up is 8,760 rows a
    year. Keep the newest few hundred of each kind."""
    async with db.connection() as conn:
        cur = await conn.execute(
            """
            DELETE FROM maintenance_runs r
             WHERE r.status IN ('succeeded', 'failed')
               AND r.id < (
                   SELECT min(id) FROM (
                       SELECT id FROM maintenance_runs
                        WHERE kind = r.kind ORDER BY id DESC LIMIT %s
                   ) newest
             )
            """,
            (keep,),
        )
        return cur.rowcount or 0


# ---------------------------------------------------------------------------
# Host facts and checklist ticks
# ---------------------------------------------------------------------------


async def get_state(key: str) -> dict[str, Any] | None:
    async with db.connection() as conn:
        cur = await conn.execute(
            "SELECT value, updated_at FROM system_state WHERE key = %s", (key,)
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return {**row["value"], "updated_at": row["updated_at"]}


async def get_states(prefix: str) -> dict[str, dict[str, Any]]:
    async with db.connection() as conn:
        cur = await conn.execute(
            "SELECT key, value, updated_at FROM system_state WHERE key LIKE %s",
            (prefix + "%",),
        )
        rows = await cur.fetchall()
    return {r["key"]: {**r["value"], "updated_at": r["updated_at"]} for r in rows}


async def set_state(key: str, value: dict[str, Any]) -> None:
    async with db.connection() as conn:
        await conn.execute(
            """
            INSERT INTO system_state (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (key, Jsonb(value)),
        )


async def clear_state(key: str) -> None:
    async with db.connection() as conn:
        await conn.execute("DELETE FROM system_state WHERE key = %s", (key,))
