"""Turning stored variables into something a build and a container can use.

Two different formats, because the two consumers parse differently and the
difference is not cosmetic:

* **The build** sources a shell file, so every value is single-quoted and
  escaped. An unquoted `PASSWORD=hunter2; rm -rf /` would otherwise be two
  shell commands.
* **The container** is given `docker --env-file`, which is *not* shell. It
  splits on the first `=` and takes the rest of the line literally — quotes
  included. Quoting a value there would put the quotes in the variable.

The one case the env-file format cannot express is a value containing a
newline, which is common enough to matter: PEM-encoded keys are the usual
example. Those are passed as arguments instead, and `_split` is where that
decision is made.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from deploypro.adapters import crypto
from deploypro.domain.models import EnvTarget
from deploypro.repositories import projects as project_repo


@dataclass(frozen=True, slots=True)
class Environment:
    """Variables split by how they can safely be delivered."""

    #: Values that fit the `KEY=value` line format.
    file_safe: dict[str, str]
    #: Values containing newlines, passed as `--env` arguments instead.
    inline: dict[str, str]

    @property
    def all(self) -> dict[str, str]:
        return {**self.file_safe, **self.inline}

    def __len__(self) -> int:
        return len(self.file_safe) + len(self.inline)


async def collect(
    project_id: UUID,
    *,
    target: EnvTarget,
    key: str,
    injected: dict[str, str] | None = None,
) -> Environment:
    """Decrypt the variables visible to `target`, plus DeployPro's own.

    A variable scoped to `production` is invisible to a preview build. That is
    the whole reason the scope exists: a preview of a branch someone opened a
    pull request from should not be able to write to the production database
    just because it was deployed from the same repository.
    """
    resolved: dict[str, str] = dict(injected or {})
    stored = await project_repo.list_env(project_id)
    variables = [var for var in stored if var.target.covers(target)]
    # `all` first, the specific scope second, so a production-only value
    # beats the same key set for all environments. Not left to row order: the
    # database returns targets in enum order (production, preview, all),
    # which once let the general value silently win.
    variables.sort(key=lambda var: var.target is not EnvTarget.ALL)
    for var in variables:
        resolved[var.key] = crypto.decrypt(var.value_encrypted, key=key)
    return _split(resolved)


def platform_variables(
    *,
    short_id: str,
    git_sha: str,
    url: str,
    target: EnvTarget,
) -> dict[str, str]:
    """What DeployPro tells every deployment about itself.

    An app that needs to build an absolute URL — a canonical tag, an OAuth
    redirect, an og:image — cannot know its own preview hostname at commit
    time. This is how it finds out at runtime.
    """
    return {
        "DEPLOYPRO_DEPLOYMENT": short_id,
        "DEPLOYPRO_GIT_SHA": git_sha,
        "DEPLOYPRO_URL": url,
        "DEPLOYPRO_ENV": target.value,
    }


def write_build_secret(env: Environment, path: Path) -> Path:
    """A file the build stage sources under `set -a`.

    Every value is single-quoted, and the one character that can end a single
    quoted string is escaped the only way a POSIX shell allows: close the
    quote, emit an escaped quote, reopen.
    """
    lines = [f"{key}={_shell_quote(value)}" for key, value in sorted(env.all.items())]
    return _write_private(path, "\n".join(lines) + ("\n" if lines else ""))


def write_runtime_env_file(env: Environment, path: Path) -> Path:
    """A file for `docker run --env-file`. Literal values, no quoting."""
    lines = [f"{key}={value}" for key, value in sorted(env.file_safe.items())]
    return _write_private(path, "\n".join(lines) + ("\n" if lines else ""))


def _split(resolved: dict[str, str]) -> Environment:
    file_safe: dict[str, str] = {}
    inline: dict[str, str] = {}
    for key, value in resolved.items():
        if "\n" in value or "\r" in value:
            inline[key] = value
        else:
            file_safe[key] = value
    return Environment(file_safe=file_safe, inline=inline)


def _shell_quote(value: str) -> str:
    escaped = value.replace("'", "'\\''")
    return f"'{escaped}'"


def _write_private(path: Path, content: str) -> Path:
    """Create the file unreadable by anyone but its owner, before it has
    content.

    Writing first and chmod'ing after leaves a window — short, but real — in
    which every plaintext secret for the project is world-readable on the host.
    Opening with the mode set closes it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    return path
