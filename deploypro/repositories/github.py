"""The GitHub App and its installations, and which projects they read.

The app's private key and webhook secret are stored encrypted and returned
encrypted: decrypting is the caller's decision, made only at the moment one
is needed, so neither travels further than the request that uses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from deploypro.adapters import db
from deploypro.domain.models import Project
from deploypro.repositories.projects import PROJECT_COLUMNS
from deploypro.repositories.rows import to_project


@dataclass(frozen=True, slots=True)
class App:
    app_id: int
    slug: str
    name: str
    html_url: str
    owner_login: str
    private_key_encrypted: bytes
    webhook_secret_encrypted: bytes


@dataclass(frozen=True, slots=True)
class Installation:
    id: int
    account_login: str
    account_type: str


async def get_app() -> App | None:
    async with db.connection() as conn:
        cur = await conn.execute(
            """
            SELECT app_id, slug, name, html_url, owner_login,
                   private_key_encrypted, webhook_secret_encrypted
              FROM github_app WHERE id = 1
            """
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return App(
        app_id=row["app_id"],
        slug=row["slug"],
        name=row["name"],
        html_url=row["html_url"],
        owner_login=row["owner_login"],
        private_key_encrypted=bytes(row["private_key_encrypted"]),
        webhook_secret_encrypted=bytes(row["webhook_secret_encrypted"]),
    )


async def save_app(
    *,
    app_id: int,
    slug: str,
    name: str,
    html_url: str,
    owner_login: str,
    private_key_encrypted: bytes,
    webhook_secret_encrypted: bytes,
) -> None:
    async with db.connection() as conn:
        await conn.execute(
            """
            INSERT INTO github_app (
                id, app_id, slug, name, html_url, owner_login,
                private_key_encrypted, webhook_secret_encrypted
            )
            VALUES (1, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                app_id,
                slug,
                name,
                html_url,
                owner_login,
                private_key_encrypted,
                webhook_secret_encrypted,
            ),
        )


async def forget_app() -> None:
    """Disconnect: the app, every installation, and every project's link to
    one. The app itself stays on GitHub until its owner deletes it there."""
    async with db.transaction() as conn:
        await conn.execute("DELETE FROM github_installations")
        await conn.execute("DELETE FROM github_app")


async def list_installations() -> list[Installation]:
    async with db.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, account_login, account_type
              FROM github_installations ORDER BY account_login
            """
        )
        rows = await cur.fetchall()
    return [Installation(r["id"], r["account_login"], r["account_type"]) for r in rows]


async def save_installation(
    installation_id: int, account_login: str, account_type: str
) -> None:
    async with db.connection() as conn:
        await conn.execute(
            """
            INSERT INTO github_installations (id, account_login, account_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE
               SET account_login = EXCLUDED.account_login,
                   account_type = EXCLUDED.account_type
            """,
            (installation_id, account_login, account_type),
        )


async def delete_installation(installation_id: int) -> None:
    async with db.connection() as conn:
        await conn.execute(
            "DELETE FROM github_installations WHERE id = %s", (installation_id,)
        )


async def link_project(project_id: UUID, installation_id: int, full_name: str) -> Project:
    async with db.connection() as conn:
        cur = await conn.execute(
            f"""
            UPDATE projects
               SET github_installation_id = %s, github_repo = %s
             WHERE id = %s
            RETURNING {PROJECT_COLUMNS}
            """,
            (installation_id, full_name, project_id),
        )
        row = await cur.fetchone()
    return to_project(row)


async def unlink_project(project_id: UUID) -> None:
    async with db.connection() as conn:
        await conn.execute(
            """
            UPDATE projects
               SET github_installation_id = NULL, github_repo = NULL
             WHERE id = %s
            """,
            (project_id,),
        )


async def projects_for_repo(full_name: str) -> list[Project]:
    """Every project linked to `owner/name`. Usually one; more when one
    repository holds several apps in different root directories."""
    async with db.connection() as conn:
        cur = await conn.execute(
            f"""
            SELECT {PROJECT_COLUMNS} FROM projects
             WHERE lower(github_repo) = lower(%s)
               AND github_installation_id IS NOT NULL
             ORDER BY name
            """,
            (full_name,),
        )
        rows = await cur.fetchall()
    return [to_project(row) for row in rows]
