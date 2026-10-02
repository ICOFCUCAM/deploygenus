"""The live-site picture of each project: requested, claimed, recorded."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from deploypro.adapters import db

COLUMNS = (
    "project_id, status, deployment_id, requested_at, started_at, "
    "captured_deployment_id, captured_at, captured_url, desktop_file, mobile_file, error"
)

#: A capture still `capturing` after this long lost its worker.
STALE_AFTER = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class SitePreview:
    project_id: UUID
    status: str
    deployment_id: UUID | None
    requested_at: datetime
    started_at: datetime | None
    captured_deployment_id: UUID | None
    captured_at: datetime | None
    captured_url: str | None
    desktop_file: str | None
    mobile_file: str | None
    error: str | None

    @property
    def in_progress(self) -> bool:
        return self.status in ("requested", "capturing")

    @property
    def has_picture(self) -> bool:
        return bool(self.desktop_file)


async def request(project_id: UUID, deployment_id: UUID | None) -> None:
    """Ask for a new picture of `deployment_id` (production). Keeps the old
    picture until the new one replaces it."""
    async with db.connection() as conn:
        await conn.execute(
            """
            INSERT INTO site_previews (project_id, status, deployment_id)
            VALUES (%s, 'requested', %s)
            ON CONFLICT (project_id) DO UPDATE
               SET status = 'requested', deployment_id = EXCLUDED.deployment_id,
                   requested_at = now(), started_at = NULL, error = NULL
            """,
            (project_id, deployment_id),
        )


async def get(project_id: UUID) -> SitePreview | None:
    async with db.connection() as conn:
        cur = await conn.execute(
            f"SELECT {COLUMNS} FROM site_previews WHERE project_id = %s", (project_id,)
        )
        row = await cur.fetchone()
    return SitePreview(**row) if row else None


async def claim() -> SitePreview | None:
    """Take the oldest requested capture, or one whose worker stopped."""
    async with db.connection() as conn:
        cur = await conn.execute(
            f"""
            UPDATE site_previews
               SET status = 'capturing', started_at = now()
             WHERE project_id = (
                   SELECT project_id FROM site_previews
                    WHERE status = 'requested'
                       OR (status = 'capturing' AND started_at < now() - %s)
                    ORDER BY requested_at
                      FOR UPDATE SKIP LOCKED
                    LIMIT 1
             )
            RETURNING {COLUMNS}
            """,
            (STALE_AFTER,),
        )
        row = await cur.fetchone()
    return SitePreview(**row) if row else None


async def captured(
    project_id: UUID,
    *,
    deployment_id: UUID | None,
    url: str,
    desktop_file: str,
    mobile_file: str | None,
) -> None:
    async with db.connection() as conn:
        await conn.execute(
            """
            UPDATE site_previews
               SET status = 'captured', captured_deployment_id = %s,
                   captured_at = now(), captured_url = %s,
                   desktop_file = %s, mobile_file = %s, error = NULL
             WHERE project_id = %s
            """,
            (deployment_id, url, desktop_file, mobile_file, project_id),
        )


async def failed(project_id: UUID, *, error: str) -> None:
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE site_previews SET status = 'failed', error = %s "
            "WHERE project_id = %s",
            (error[:500], project_id),
        )
