"""Dumping the platform's own database.

`pg_dump` must be at least as new as the server it dumps, and the control
plane image carries no Postgres client at all. So by default the dump runs in
a throwaway container of the same image the database runs — `postgres:16-alpine`
in docker-compose.yml — on the same network, which matches the versions by
construction. A `pg_dump` on PATH is used instead when there is one, which is
the case on a developer's machine and in the end-to-end run.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

#: The image the database runs, so the client matches the server.
DEFAULT_IMAGE = "postgres:16-alpine"


class DumpFailed(RuntimeError):
    pass


async def dump(database_url: str, dest: Path, *, network: str) -> str:
    """Write a custom-format dump (`pg_restore` reads it) to `dest`.

    Returns how it was taken, for the backup's manifest.
    """
    local = shutil.which("pg_dump")
    if local:
        argv = [local, "--format=custom", "--no-owner", database_url]
        how = "pg_dump on the host"
    else:
        image = os.environ.get("FORGE_PG_DUMP_IMAGE") or DEFAULT_IMAGE
        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            image,
            "pg_dump",
            "--format=custom",
            "--no-owner",
            database_url,
        ]
        how = f"pg_dump in {image}"

    with dest.open("wb") as out:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=out, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
    if proc.returncode != 0:
        # The URL carries the password; the error must not.
        message = (
            stderr.decode(errors="replace")
            .strip()
            .replace(database_url, "<DATABASE_URL>")
        )
        raise DumpFailed(f"{how} failed: {message}")
    return how
