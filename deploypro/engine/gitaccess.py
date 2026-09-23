"""How git reaches a project's repository: through the GitHub App, or over
SSH with its deploy key, and only ever to a host whose key it recognises.

The private key is decrypted into a 0600 file in a fresh 0700 directory for
the length of one git command, and removed however that command ends. It is
never in the process environment, never on a command line (only its path
is), and never in a log.

Host keys: GitHub's are pinned (see deploypro.domain.repo_url), so a clone
from github.com fails rather than trusting an impostor. Any other SSH host is
trusted on first contact and remembered, so a key that changes later is
refused. Both live in one known_hosts file under the build root, which
survives restarts.
"""

from __future__ import annotations

import shlex
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from deploypro.adapters import crypto, sshkeys
from deploypro.config import Settings
from deploypro.domain.models import Project
from deploypro.domain.repo_url import GITHUB_KNOWN_HOSTS, is_ssh_url
from deploypro.repositories import projects as project_repo


def known_hosts_file(settings: Settings) -> Path:
    """The platform's known_hosts, with GitHub's keys always in it."""
    directory = settings.build_root / "ssh"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "known_hosts"
    existing = path.read_text().splitlines() if path.exists() else []
    missing = [line for line in GITHUB_KNOWN_HOSTS if line not in existing]
    if missing:
        path.write_text("\n".join([*existing, *missing]) + "\n")
    return path


def ssh_command(*, known_hosts: Path, identity: Path | None) -> str:
    parts = [
        "ssh",
        # Never prompt: a worker has no one to answer.
        "-o", "BatchMode=yes",
        # Unknown hosts are remembered on first use; a known host whose key
        # has changed is refused. GitHub is known from the start.
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={known_hosts}",
    ]  # fmt: skip
    if identity is not None:
        # Only this key. Without IdentitiesOnly, ssh also offers any key an
        # agent holds, and GitHub accepts the first that works — which could
        # be a key for a different repository.
        parts += ["-i", str(identity), "-o", "IdentitiesOnly=yes"]
    return " ".join(shlex.quote(part) for part in parts)


@asynccontextmanager
async def git_env(project: Project, settings: Settings) -> AsyncIterator[dict[str, str]]:
    """Environment for git commands against this project's repository."""
    if project.github_installation_id is not None and not is_ssh_url(project.repo_url):
        # Imported through the GitHub App: an hour-long token that reads this
        # one repository, sent as a header (see domain.github.git_auth_env).
        from deploypro.engine import github

        yield await github.git_env(project, settings)
        return

    if not is_ssh_url(project.repo_url):
        yield {}
        return

    known_hosts = known_hosts_file(settings)
    encrypted = await project_repo.get_deploy_key_encrypted(project.id)
    if encrypted is None:
        yield {"GIT_SSH_COMMAND": ssh_command(known_hosts=known_hosts, identity=None)}
        return

    workdir = Path(tempfile.mkdtemp(prefix="key-", dir=known_hosts.parent))
    try:
        identity = workdir / "id_ed25519"
        identity.touch(mode=0o600)
        identity.write_text(crypto.decrypt(encrypted, key=settings.master_key))
        yield {"GIT_SSH_COMMAND": ssh_command(known_hosts=known_hosts, identity=identity)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def ensure_key(
    project: Project, settings: Settings, *, rotate: bool = False
) -> Project:
    """Give the project a deploy key, or a new one. Returns the updated project.

    Rotating makes the old key useless at once, so the new public key has to
    be added to the repository (and the old one removed) before the next
    deploy.
    """
    if project.deploy_key_public and not rotate:
        return project
    private, public = sshkeys.generate(f"deploypro@{project.slug}")
    return await project_repo.set_deploy_key(
        project.id,
        public=public,
        encrypted=crypto.encrypt(private, key=settings.master_key),
    )


def instructions(project: Project) -> list[str]:
    """What to do with the public key, in the words GitHub's settings use."""
    lines = [
        "Add it to the repository as a deploy key:",
        "  GitHub → the repository → Settings → Deploy keys → Add deploy key",
        f"  Title: DeployPro ({project.slug})",
        "  Leave 'Allow write access' OFF — DeployPro only ever reads.",
    ]
    if not is_ssh_url(project.repo_url):
        lines += [
            "",
            "The project's repository URL is not an SSH URL, so the key is not",
            "used yet. Switch it to the SSH form, e.g. git@github.com:owner/repo.git",
        ]
    return lines
