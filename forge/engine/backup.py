"""Backing up what cannot be rebuilt.

Almost nothing on a Forge host needs a backup. Images rebuild from git,
containers restart from images, router files are rewritten on the next
promotion. Two things are different: the database (projects, encrypted
variables, domains, history) and the volumes (whatever the apps keep).
A backup is exactly those two, written to one directory per run:

    forge-20260923T030000Z/
      manifest.json             what is here, with sizes and SHA-256
      forge.dump                pg_dump custom format; restore with pg_restore
      volumes/<docker volume>.tar.gz

It is written under a `.partial` name and renamed only when complete, so a
backup interrupted halfway never looks like one that finished.

**The master key is not in it.** Every environment variable in the dump is
encrypted with FORGE_MASTER_KEY; a backup that carried the key would carry
every secret in plaintext-equivalent form to wherever backups get copied.
Keep the key somewhere else — a password manager — and keep it as carefully
as the backups, because the dump's variables are unreadable without it.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from forge.adapters import containers, db, pgdump
from forge.config import Settings
from forge.domain.storage import docker_volume_name
from forge.repositories import deployments as deployment_repo
from forge.repositories import projects as project_repo
from forge.repositories import volumes as volume_repo

PREFIX = "forge-"
PARTIAL = ".partial"

#: Held for the length of a backup, so two workers noticing the backup hour
#: at the same moment produce one backup. Any constant unique to this use.
ADVISORY_LOCK = 0x466F726765  # "Forge"


class BackupSkipped(RuntimeError):
    """Another worker is already taking one."""


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    volumes: list[str]
    bytes: int
    removed: list[Path]


async def take(settings: Settings, dest: Path, *, keep: int) -> BackupResult:
    """Write one complete backup into `dest`, then prune to the newest `keep`."""
    async with db.connection() as conn:
        cur = await conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS got", (ADVISORY_LOCK,)
        )
        if not (await cur.fetchone())["got"]:
            raise BackupSkipped("another backup is in progress")
        try:
            result = await _take(settings, dest)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK,))
    removed = prune(dest, keep=keep)
    return BackupResult(result.path, result.volumes, result.bytes, removed)


async def _take(settings: Settings, dest: Path) -> BackupResult:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    final = dest / f"{PREFIX}{stamp}"
    work = dest / f"{PREFIX}{stamp}{PARTIAL}"
    shutil.rmtree(work, ignore_errors=True)
    (work / "volumes").mkdir(parents=True)

    try:
        how = await pgdump.dump(
            settings.database_url, work / "forge.dump", network=settings.network
        )
        # Compression and hashing run on a thread: a volume of video is
        # gigabytes, and doing either on the event loop would stall the
        # scheduler and the draining sweep for the whole backup.
        files = {"forge.dump": await asyncio.to_thread(_describe, work / "forge.dump")}

        exported = []
        present = set(await containers.list_owned_volumes())
        for project in await project_repo.list_all():
            volumes = await volume_repo.list_for_project(project.id)
            if not volumes:
                continue
            image = await _any_image(project)
            for volume in volumes:
                name = docker_volume_name(project.slug, volume.name)
                if name not in present:
                    continue  # configured, never mounted yet: nothing to save
                if image is None:
                    raise RuntimeError(
                        f"{name} exists but {project.slug} has no image on this "
                        "host to read it through"
                    )
                tar = work / "volumes" / f"{name}.tar"
                await containers.export_volume(name, image, tar)
                gz = await asyncio.to_thread(_gzip, tar)
                files[f"volumes/{gz.name}"] = await asyncio.to_thread(_describe, gz)
                exported.append(name)

        manifest = {
            "format": 1,
            "taken_at": stamp,
            "database": {"file": "forge.dump", "taken_with": how},
            "volumes": exported,
            "files": files,
            "master_key_included": False,
            "restore": "See README.md, Backups, in the Forge repository.",
        }
        (work / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        work.rename(final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise

    size = sum(f.stat().st_size for f in final.rglob("*") if f.is_file())
    return BackupResult(final, exported, size, [])


def prune(dest: Path, *, keep: int) -> list[Path]:
    """Keep the newest `keep` complete backups; remove older ones and any
    `.partial` left by a run that died."""
    removed = []
    complete = sorted(
        (
            p
            for p in dest.glob(f"{PREFIX}*")
            if p.is_dir() and not p.name.endswith(PARTIAL)
        ),
        reverse=True,
    )
    for old in complete[keep:]:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old)
    return removed


def latest(dest: Path) -> Path | None:
    complete = sorted(
        (
            p
            for p in dest.glob(f"{PREFIX}*")
            if p.is_dir() and not p.name.endswith(PARTIAL)
        ),
        reverse=True,
    )
    return complete[0] if complete else None


def taken_today(dest: Path, *, now: datetime | None = None) -> bool:
    newest = latest(dest)
    if newest is None:
        return False
    today = (now or datetime.now(UTC)).strftime("%Y%m%d")
    return newest.name.removeprefix(PREFIX).startswith(today)


async def restore_volume(backup: Path, *, project_slug: str, volume: str) -> str:
    """Unpack one volume from a backup into its Docker volume.

    Adds and overwrites files; it deletes nothing, so restoring into a volume
    that has moved on keeps what is newer. Stop the project's workers first if
    they might be writing the same files.
    """
    name = docker_volume_name(project_slug, volume)
    gz = backup / "volumes" / f"{name}.tar.gz"
    if not gz.exists():
        raise FileNotFoundError(f"{backup.name} has no copy of {name}")
    project = await project_repo.get_by_slug(project_slug)
    image = await _any_image(project)
    if image is None:
        raise RuntimeError(f"{project_slug} has no image on this host to restore through")
    await containers.ensure_volume(
        name,
        labels={
            containers.OWNER_LABEL: containers.OWNER_VALUE,
            containers.PROJECT_LABEL: project_slug,
            "forge.volume": volume,
        },
    )
    tar = gz.with_suffix("")  # .tar next to it, removed afterwards
    try:
        await asyncio.to_thread(_gunzip, gz, tar)
        await containers.import_volume(name, image, tar)
    finally:
        tar.unlink(missing_ok=True)
    return name


async def _any_image(project) -> str | None:
    """An image of this project that exists on the host. Production's if it
    can, but any will do: it is never run, only used to reach the volume."""
    candidates = []
    if project.production_deployment_id:
        candidates.append(
            (await deployment_repo.get(project.production_deployment_id)).image_tag
        )
    candidates += [
        d.image_tag for d in await deployment_repo.list_for_project(project.id, limit=50)
    ]
    for tag in candidates:
        if tag and await containers.image_exists(tag):
            return tag
    return None


def _gzip(tar: Path) -> Path:
    gz = tar.with_name(tar.name + ".gz")
    with tar.open("rb") as src, gzip.open(gz, "wb", compresslevel=6) as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)
    tar.unlink()
    return gz


def _gunzip(gz: Path, tar: Path) -> None:
    with gzip.open(gz, "rb") as src, tar.open("wb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)


def _describe(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}
