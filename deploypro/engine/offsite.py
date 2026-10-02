"""Copying backups off this server, to a Hetzner Storage Box or any SFTP host.

A backup on the server it protects is lost with that server. After every
backup the worker copies it to the target over SFTP, and keeps the newest
DEPLOYPRO_BACKUP_KEEP there too. docs/design/proposal-offsite-backups.md.

- **DeployPro's own key.** An Ed25519 key is made for this target and kept
  encrypted with the master key. The owner's Storage Box password is used
  once, to install that key, and never stored.
- **Copy, then rename.** A backup is uploaded under `.partial` and renamed
  when complete, as on the server, so an interrupted copy never looks done.
- **Never mirrored.** Pruning deletes only copies older than the newest
  `keep` that are there. An emptied backup directory on the server deletes
  nothing on the target.
- **The master key is still not in it.** The copy is the same directory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deploypro.adapters import crypto, sftp, sshkeys
from deploypro.config import Settings
from deploypro.domain.errors import Conflict, DeployProError, InvalidRequest, OffsiteError
from deploypro.engine import backup
from deploypro.repositories import maintenance as maintenance_repo

STATE_KEY = "offsite"
STORAGE_BOX_HOST = re.compile(r"^(u\d+)(-sub\d+)?$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FOLDER = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
COPY_TIMEOUT = 3 * 3600
#: Marks DeployPro's own line in the target's authorized_keys.
KEY_COMMENT = "deploypro-backups"


class CopyFailed(OffsiteError):
    """The backup was taken, but not copied off the server."""


@dataclass(frozen=True, slots=True)
class Target:
    user: str
    host: str
    port: int
    folder: str
    public_key: str
    private_key_encrypted: str
    known_hosts: str = ""
    #: True once a key login worked.
    connected: bool = False
    #: The last copy: {"at", "ok", "name", "error"}.
    last: dict[str, Any] | None = None

    @property
    def address(self) -> str:
        port = "" if self.port == 22 else f":{self.port}"
        return f"{self.user}@{self.host}{port}"

    def as_state(self) -> dict[str, Any]:
        return {
            "user": self.user,
            "host": self.host,
            "port": self.port,
            "folder": self.folder,
            "public_key": self.public_key,
            "private_key": self.private_key_encrypted,
            "known_hosts": self.known_hosts,
            "connected": self.connected,
            "last": self.last,
        }


def _from_state(value: dict[str, Any]) -> Target:
    return Target(
        user=value["user"],
        host=value["host"],
        port=int(value["port"]),
        folder=value["folder"],
        public_key=value["public_key"],
        private_key_encrypted=value["private_key"],
        known_hosts=value.get("known_hosts") or "",
        connected=bool(value.get("connected")),
        last=value.get("last"),
    )


async def get() -> Target | None:
    value = await maintenance_repo.get_state(STATE_KEY)
    if not value or "user" not in value:
        return None
    return _from_state(value)


async def _save(target: Target) -> None:
    await maintenance_repo.set_state(STATE_KEY, target.as_state())


def normalise(user: str, host: str, port: str, folder: str) -> tuple[str, str, int, str]:
    """Checked values; a bare Storage Box username fills in its host."""
    user, host, folder = user.strip(), host.strip().lower(), folder.strip().strip("/")
    if "@" in user and not host:
        user, host = user.split("@", 1)
    if not _USER.match(user):
        raise InvalidRequest("Enter the username, like u123456.")
    box = STORAGE_BOX_HOST.match(user)
    if not host:
        if box is None:
            raise InvalidRequest("Enter the server's address.")
        host = f"{box.group(1)}.your-storagebox.de"
    if not _HOST.match(host):
        raise InvalidRequest(
            "The address should be a host name, like u123456.your-storagebox.de."
        )
    try:
        number = int(port.strip() or sftp.STORAGE_BOX_PORT)
    except ValueError:
        raise InvalidRequest("The port is a number, 23 for a Storage Box.") from None
    if not 1 <= number <= 65535:
        raise InvalidRequest("The port is a number, 23 for a Storage Box.")
    folder = folder or "deploypro"
    if not _FOLDER.match(folder) or ".." in folder.split("/"):
        raise InvalidRequest("The folder may use letters, digits, - _ . and /.")
    return user, host, number, folder


async def configure(
    settings: Settings, *, user: str, host: str, port: str, folder: str
) -> Target:
    """Save the target. The key is kept if there is one; a new server means
    its host key is learned again."""
    user, host, number, folder = normalise(user, host, port, folder)
    existing = await get()
    if existing is not None:
        same_server = (existing.host, existing.port) == (host, number)
        target = replace(
            existing,
            user=user,
            host=host,
            port=number,
            folder=folder,
            known_hosts=existing.known_hosts if same_server else "",
            connected=existing.connected and same_server and existing.user == user,
        )
    else:
        private, public = sshkeys.generate(KEY_COMMENT)
        target = Target(
            user=user,
            host=host,
            port=number,
            folder=folder,
            public_key=public,
            private_key_encrypted=crypto.encrypt(
                private, key=settings.master_key
            ).decode(),
        )
    await _save(target)
    return target


async def remove() -> None:
    await maintenance_repo.clear_state(STATE_KEY)


def _private_key(settings: Settings, target: Target) -> str:
    return crypto.decrypt(target.private_key_encrypted.encode(), key=settings.master_key)


async def _run(
    settings: Settings, target: Target, commands: list[str], **kw
) -> sftp.Result:
    try:
        result = await sftp.run(
            host=target.host,
            port=target.port,
            user=target.user,
            commands=commands,
            known_hosts=target.known_hosts,
            private_key="" if "password" in kw else _private_key(settings, target),
            **kw,
        )
    except sftp.SftpFailed as exc:
        raise OffsiteError(_explain(target, str(exc), password="password" in kw)) from exc
    if result.known_hosts != target.known_hosts and not target.known_hosts:
        target = replace(target, known_hosts=result.known_hosts)
        await _save(target)
    return result


def _explain(target: Target, said: str, *, password: bool) -> str:
    where = target.address
    if "Host key" in said or "IDENTIFICATION HAS CHANGED" in said:
        return (
            f"{where} is not the server DeployPro first connected to: its host key "
            "changed. If you replaced the Storage Box, save its settings again."
        )
    if "Permission denied" in said:
        if password:
            return (
                f"{where} refused the password. Check it, and that SSH support is "
                "on for the Storage Box in Hetzner Console."
            )
        return f"{where} refused DeployPro's key. Install it again with the password."
    return f"{where}: {said}"


async def install_key(settings: Settings, password: str) -> Target:
    """Add DeployPro's key to the server's .ssh/authorized_keys, with the
    password used this once. Then log in with the key, to prove it."""
    target = await get()
    if target is None:
        raise Conflict("Set up the Storage Box first.")
    if not password:
        raise InvalidRequest("Enter the Storage Box password.")
    fetched = await _run(
        settings,
        target,
        ["-mkdir .ssh", "-get .ssh/authorized_keys authorized_keys"],
        password=password,
    )
    target = await get() or target  # the host key may have just been learned
    existing = fetched.files.get("authorized_keys", "")
    lines = [line for line in existing.splitlines() if line.strip()]
    # DeployPro's earlier keys go (a set-up turned off and on again); the
    # owner's own lines stay exactly as they were.
    kept = [line for line in lines if not line.endswith(f" {KEY_COMMENT}")]
    if kept + [target.public_key] != lines:
        await _run(
            settings,
            target,
            [
                "put authorized_keys .ssh/authorized_keys",
                "-chmod 600 .ssh/authorized_keys",
            ],
            password=password,
            local={"authorized_keys": "\n".join([*kept, target.public_key]) + "\n"},
        )
    return await test(settings)


async def test(settings: Settings) -> Target:
    """Log in with the key and make sure the folder is there."""
    target = await get()
    if target is None:
        raise Conflict("Set up the Storage Box first.")
    try:
        await _run(settings, target, [*_mkdirs(target.folder), f"ls {target.folder}"])
    except DeployProError:
        target = await get() or target
        if target.connected:
            await _save(replace(target, connected=False))
        raise
    target = replace(await get() or target, connected=True)
    await _save(target)
    return target


def _mkdirs(folder: str) -> list[str]:
    parts = folder.split("/")
    return [f"-mkdir {'/'.join(parts[: i + 1])}" for i in range(len(parts))]


async def copy(settings: Settings, local: Path, *, keep: int) -> str:
    """Upload one finished backup, then prune the target to the newest `keep`.
    Returns one sentence for people; raises DeployProError on failure, after
    recording it."""
    target = await get()
    if target is None:
        return ""
    name = local.name
    started = datetime.now(UTC)
    try:
        if not target.connected:
            raise OffsiteError("Not connected yet: install the key on the System page.")
        partial = f"{target.folder}/{name}{backup.PARTIAL}"
        await _run(
            settings,
            target,
            [
                *_mkdirs(target.folder),
                *_remove_dir(partial),
                f'put -r "{local}" {partial}',
                # A retried copy: the complete new upload replaces the old.
                *_remove_dir(f"{target.folder}/{name}"),
                f"rename {partial} {target.folder}/{name}",
            ],
            timeout=COPY_TIMEOUT,
        )
        removed = await _prune(settings, target, keep=keep)
    except DeployProError as exc:
        await _record(started, ok=False, name=name, error=exc.message)
        raise CopyFailed(exc.message) from exc
    await _record(started, ok=True, name=name, error="")
    said = f"Copied {name} to {target.address}."
    if removed:
        said += f" Removed {len(removed)} older copy(s) there."
    return said


async def _prune(settings: Settings, target: Target, *, keep: int) -> list[str]:
    listing = await _run(settings, target, [f"ls -1 {target.folder}"])
    names = sorted(
        {
            line.strip().rsplit("/", 1)[-1]
            for line in listing.output.splitlines()
            if not line.startswith("sftp>")
        }
    )
    finished = [
        n for n in names if n.startswith(backup.PREFIX) and not n.endswith(backup.PARTIAL)
    ]
    old = finished[:-keep] if keep > 0 else []
    if not old:
        return []
    commands = [c for n in old for c in _remove_dir(f"{target.folder}/{n}")]
    await _run(settings, target, commands)
    return old


def _remove_dir(path: str) -> list[str]:
    """A backup directory is files plus volumes/ (deploypro.engine.backup);
    sftp has no recursive delete."""
    return [
        f"-rm {path}/volumes/*",
        f"-rmdir {path}/volumes",
        f"-rm {path}/*",
        f"-rmdir {path}",
    ]


async def _record(started: datetime, *, ok: bool, name: str, error: str) -> None:
    target = await get()
    if target is None:
        return
    await _save(
        replace(
            target,
            last={"at": started.isoformat(), "ok": ok, "name": name, "error": error},
        )
    )
