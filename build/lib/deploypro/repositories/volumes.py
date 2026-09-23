"""A project's persistent volumes — the configuration, not the data.

The data lives in Docker volumes this module never touches. Removing a row
stops the volume being mounted on the next deploy and leaves every byte of it
where it was.
"""

from __future__ import annotations

from uuid import UUID

from deploypro.adapters import db
from deploypro.domain.errors import Conflict, NotFound
from deploypro.domain.models import Volume
from deploypro.repositories.rows import to_volume

COLUMNS = "id, project_id, name, mount_path, created_at"


async def create(*, project_id: UUID, name: str, mount_path: str) -> Volume:
    async with db.connection() as conn:
        try:
            cur = await conn.execute(
                f"""
                INSERT INTO volumes (project_id, name, mount_path)
                VALUES (%s, %s, %s)
                RETURNING {COLUMNS}
                """,
                (project_id, name, mount_path),
            )
        except Exception as exc:  # noqa: BLE001 - narrowed immediately below
            if "volumes_project_id_name_key" in str(exc):
                raise Conflict(
                    f"This project already has a volume called {name!r}"
                ) from exc
            if "volumes_project_id_mount_path_key" in str(exc):
                raise Conflict(
                    f"Another volume of this project is already mounted at {mount_path}"
                ) from exc
            raise
        row = await cur.fetchone()
    return to_volume(row)


async def list_for_project(project_id: UUID) -> list[Volume]:
    async with db.connection() as conn:
        cur = await conn.execute(
            f"SELECT {COLUMNS} FROM volumes WHERE project_id = %s ORDER BY mount_path",
            (project_id,),
        )
        rows = await cur.fetchall()
    return [to_volume(row) for row in rows]


async def delete(project_id: UUID, name: str) -> Volume:
    async with db.connection() as conn:
        cur = await conn.execute(
            f"DELETE FROM volumes WHERE project_id = %s AND name = %s "
            f"RETURNING {COLUMNS}",
            (project_id, name),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFound(f"This project has no volume called {name!r}")
    return to_volume(row)
