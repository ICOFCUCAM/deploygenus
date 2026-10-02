"""Running an SFTP batch against one server.

Used for the off-server copy of backups (deploypro.engine.offsite). The
OpenSSH `sftp` client is already in the image for git; no rsync, no library.

Credentials never reach a command line or a log:
- a private key is written to a 0600 file in a fresh 0700 directory for the
  length of one batch and removed however it ends;
- a password (used once, to install the key) reaches ssh through an askpass
  script that reads it from the child's environment.

Host keys: trusted on first contact, then pinned. The caller passes what it
remembered (empty the first time) and gets back what ssh recorded.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: The Storage Box's SSH/SFTP port for keys and the restricted shell.
STORAGE_BOX_PORT = 23

ASKPASS = '#!/bin/sh\nprintf "%s\\n" "$DEPLOYPRO_SFTP_PASSWORD"\n'


class SftpFailed(RuntimeError):
    """The batch did not complete; the message is sftp's own last words."""


@dataclass(frozen=True, slots=True)
class Result:
    output: str
    files: dict[str, str]
    #: known_hosts lines after the batch: what was passed in, plus the
    #: server's key if this was the first contact.
    known_hosts: str


async def run(
    *,
    host: str,
    port: int,
    user: str,
    commands: list[str],
    known_hosts: str,
    private_key: str = "",
    password: str = "",
    local: dict[str, str] | None = None,
    timeout: float = 60,
) -> Result:
    """Run `commands` as one sftp batch. A command starting with "-" may fail
    without failing the batch (sftp's own rule).

    `local` files are written into the batch's working directory first, so
    relative paths in `put` find them; every file there afterwards, including
    what `get` fetched, comes back in `Result.files`."""
    workdir = Path(tempfile.mkdtemp(prefix="deploypro-sftp-"))
    try:
        workdir.chmod(0o700)
        hosts = workdir / "known_hosts"
        hosts.write_text(known_hosts)
        here = workdir / "local"
        here.mkdir()
        for name, content in (local or {}).items():
            (here / name).write_text(content)
        batch = workdir / "batch"
        batch.write_text("\n".join(commands) + "\n")
        args = ["sftp"]
        if password:
            # `-b` turns BatchMode on, which skips password logins. ssh keeps
            # the first value it is given for an option, so this goes first.
            args += ["-o", "BatchMode=no"]
        args += [
            "-b", str(batch),
            "-P", str(port),
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"UserKnownHostsFile={hosts}",
            "-o", "ConnectTimeout=20",
            "-o", "ServerAliveInterval=30",
        ]  # fmt: skip
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(workdir)}
        if private_key:
            identity = workdir / "id"
            identity.touch(mode=0o600)
            identity.write_text(private_key)
            args += [
                "-i", str(identity),
                "-o", "IdentitiesOnly=yes",
                "-o", "PasswordAuthentication=no",
                "-o", "KbdInteractiveAuthentication=no",
                "-o", "BatchMode=yes",
            ]  # fmt: skip
        else:
            askpass = workdir / "askpass"
            askpass.write_text(ASKPASS)
            askpass.chmod(0o700)
            env |= {
                "SSH_ASKPASS": str(askpass),
                "SSH_ASKPASS_REQUIRE": "force",
                "DISPLAY": "none",
                "DEPLOYPRO_SFTP_PASSWORD": password,
            }
            args += [
                "-o", "PubkeyAuthentication=no",
                "-o", "PreferredAuthentications=password,keyboard-interactive",
                "-o", "NumberOfPasswordPrompts=1",
            ]  # fmt: skip
        args.append(f"{user}@{host}")
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=here,
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise SftpFailed(f"no answer from {host} within {int(timeout)} s") from None
        output = out.decode(errors="replace")
        if proc.returncode != 0:
            raise SftpFailed(_last_words(output) or f"sftp exited {proc.returncode}")
        files = {f.name: f.read_text() for f in here.iterdir() if f.is_file()}
        return Result(output, files, hosts.read_text())
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


#: sftp's own words that say what went wrong, before its "Connection closed".
TELLING = (
    "Host key verification failed",
    "REMOTE HOST IDENTIFICATION HAS CHANGED",
    "Permission denied",
    "Could not resolve hostname",
    "Connection refused",
    "Connection timed out",
    "No route to host",
)


def _last_words(output: str) -> str:
    lines = [
        line.strip()
        for line in output.splitlines()
        if line.strip() and not line.startswith("sftp>")
    ]
    for words in TELLING:
        for line in lines:
            if words in line:
                return line.strip("@ ").strip()
    return lines[-1] if lines else ""
