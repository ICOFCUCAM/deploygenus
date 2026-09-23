"""Persistent volumes, and the rules a mount path has to obey.

A deployment is immutable and disposable; a volume is neither. It is the one
thing a project owns that outlives every container it has ever run, which is
why the rules here are strict: a mount path that shadows `/etc` or `/proc`
breaks the container in ways that look nothing like a storage problem, and a
volume name that two projects could both arrive at would hand one project's
data to the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from deploypro.domain.errors import InvalidRequest

#: Same shape as a process name: it ends up in a Docker object name.
_VOLUME_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")

#: Characters a mount path may contain. Deliberately narrow — no comma,
#: because `docker run --mount` is a comma-separated list and a comma in the
#: path would be read as the start of another option.
_PATH_CHARS = re.compile(r"^/[A-Za-z0-9._/-]*$")

#: Paths that belong to the container runtime or the operating system. A
#: volume over any of them hides what the image put there, so the container
#: either fails to start or starts and misbehaves.
_RESERVED = ("/proc", "/sys", "/dev", "/etc", "/bin", "/sbin", "/lib", "/usr")

#: Separator in the Docker volume name. Underscore because it can appear in
#: neither a project slug nor a volume name, so `deploypro_<slug>_<name>` is
#: unambiguous: slug `a-b` with volume `c` and slug `a` with volume `b-c`
#: cannot collide, as they would with a hyphen.
_SEPARATOR = "_"


@dataclass(frozen=True, slots=True)
class Mount:
    """A Docker volume at a path inside a container."""

    volume: str
    path: str


def validate_volume_name(name: str) -> str:
    cleaned = name.strip().lower()
    if not _VOLUME_NAME.match(cleaned):
        raise InvalidRequest(
            "A volume name is lowercase letters, digits and hyphens, "
            "up to 32 characters, e.g. 'recordings'"
        )
    return cleaned


def normalise_mount_path(path: str) -> str:
    """Validate a container path and return it in one canonical form.

    Canonical matters because uniqueness is checked on the stored string:
    `/data/` and `/data` are the same mount point, and two volumes claiming it
    would leave one of them silently unmounted.
    """
    cleaned = path.strip()
    if not cleaned.startswith("/"):
        raise InvalidRequest("A mount path must be absolute, e.g. /data")
    if not _PATH_CHARS.match(cleaned):
        raise InvalidRequest(
            "A mount path may contain letters, digits, '.', '_', '-' and '/' only"
        )
    parts = [part for part in cleaned.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise InvalidRequest("A mount path may not contain '.' or '..' segments")
    if not parts:
        raise InvalidRequest("A volume cannot be mounted over the whole filesystem")
    canonical = "/" + "/".join(parts)
    for reserved in _RESERVED:
        if canonical == reserved or canonical.startswith(reserved + "/"):
            raise InvalidRequest(
                f"{canonical} is inside {reserved}, which belongs to the "
                "operating system. Mount somewhere like /data instead."
            )
    return canonical


def docker_volume_name(project_slug: str, volume: str) -> str:
    """`balancevid` + `recordings` -> `deploypro_balancevid_recordings`.

    Named after the project, not a deployment, because that is the point of a
    volume: deployment 14 and deployment 15 see the same data.
    """
    return f"deploypro{_SEPARATOR}{project_slug}{_SEPARATOR}{volume}"


# ---------------------------------------------------------------------------
# Draining
# ---------------------------------------------------------------------------
#
# A container being replaced is renamed rather than waited on. The rename
# frees its name at once, so its successor can start immediately, and the
# new name carries the moment after which it is killed — which means no
# database row and no in-memory timer has to survive for the deadline to be
# enforced. Whatever process finds the container later can read the deadline
# off the container itself.

_DRAINING = re.compile(r"^(?P<name>.+)\.draining\.(?P<deadline>\d+)$")


def draining_name(name: str, *, deadline: int) -> str:
    """`deploypro-blog-mailer-0` -> `deploypro-blog-mailer-0.draining.1790000000`."""
    return f"{name}.draining.{deadline}"


def draining_deadline(name: str) -> int | None:
    """The Unix time a draining container is killed at, or None if it is not
    draining."""
    match = _DRAINING.match(name.lstrip("/"))
    return int(match["deadline"]) if match else None
