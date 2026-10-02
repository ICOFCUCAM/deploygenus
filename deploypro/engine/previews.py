"""A picture of each project's live site, for its Overview.

docs/design/amendment-overview-preview.md. Taken when production changes
(promotion or rollback) or when the owner presses Refresh — never for
previews — through the PUBLIC address, so it shows what a visitor gets:
the primary verified domain if there is one, else the deployment's own
address.

Two sizes, desktop and mobile, each in a short-lived capped browser
container (`containers.screenshot`). A failure keeps the previous picture
and says why; it never touches the deploy.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from deploypro.adapters import containers
from deploypro.config import Settings
from deploypro.domain.models import Deployment, Domain, Project
from deploypro.repositories import deployments as deployment_repo
from deploypro.repositories import previews as preview_repo
from deploypro.repositories import projects as project_repo

logger = logging.getLogger("deploypro.previews")

DESKTOP = (1440, 900)
MOBILE = (390, 844)

#: How long the site gets to answer before the browser is started at all.
REACH_TIMEOUT = 15.0


def enabled(settings: Settings) -> bool:
    return bool(settings.preview_image)


def address(
    project: Project, deployment: Deployment, domains: list[Domain], settings
) -> str:
    """The public address a visitor uses: the primary verified domain, else
    any verified one, else the deployment's own."""
    verified = [d for d in domains if d.is_verified]
    primary = next((d for d in verified if d.is_primary), None) or (
        verified[0] if verified else None
    )
    if primary is not None:
        return f"https://{primary.host}/"
    return settings.deployment_url(deployment.short_id)


async def request(settings: Settings, project: Project, deployment: Deployment) -> None:
    """Production changed: ask for a new picture. Never raises — a picture is
    not worth failing a promotion over."""
    try:
        if not enabled(settings):
            return
        await preview_repo.request(project.id, deployment.id)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("could not request a preview for %s", project.slug)


async def capture_next(settings: Settings) -> bool:
    """Capture the oldest requested picture, if any. True if one was taken
    (or tried)."""
    row = await preview_repo.claim()
    if row is None:
        return False
    try:
        await _capture(settings, row)
    except Exception as exc:  # noqa: BLE001 - recorded on the row
        logger.exception("preview capture failed")
        await preview_repo.failed(row.project_id, error=_reason(exc))
    return True


async def _capture(settings: Settings, row) -> None:
    project = await project_repo.get(row.project_id)
    deployment_id = row.deployment_id or project.production_deployment_id
    if deployment_id is None:
        await preview_repo.failed(project.id, error="Nothing is in production.")
        return
    deployment = await deployment_repo.get(deployment_id)
    url = address(
        project, deployment, await project_repo.list_domains(project.id), settings
    )

    # The browser would photograph an error page and call it a success.
    try:
        async with httpx.AsyncClient(timeout=REACH_TIMEOUT, follow_redirects=True) as c:
            answer = await c.get(url)
    except httpx.HTTPError as exc:
        await preview_repo.failed(
            project.id, error=f"{url} did not answer: {type(exc).__name__}"
        )
        return
    if answer.status_code >= 500:
        await preview_repo.failed(
            project.id, error=f"{url} answered {answer.status_code}"
        )
        return

    await containers.ensure_image(settings.preview_image)
    settings.previews_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    base = f"{project.slug}-{deployment.number}-{stamp}"
    desktop = f"{base}-desktop.png"
    await containers.screenshot(
        settings.preview_image,
        url,
        settings.previews_dir / desktop,
        width=DESKTOP[0],
        height=DESKTOP[1],
    )
    mobile: str | None = f"{base}-mobile.png"
    try:
        await containers.screenshot(
            settings.preview_image,
            url,
            settings.previews_dir / mobile,
            width=MOBILE[0],
            height=MOBILE[1],
            mobile=True,
        )
    except containers.DockerError:
        logger.warning("mobile preview of %s failed; keeping desktop", project.slug)
        mobile = None

    await preview_repo.captured(
        project.id,
        deployment_id=deployment.id,
        url=url,
        desktop_file=desktop,
        mobile_file=mobile,
    )
    _sweep(settings, project.slug, keep={desktop, mobile})
    logger.info("captured a preview of %s at %s", project.slug, url)


def _sweep(settings: Settings, slug: str, *, keep: set[str | None]) -> None:
    """Only the current pair is kept: an old picture is never shown again."""
    for path in settings.previews_dir.glob(f"{slug}-*.png"):
        if path.name not in keep:
            path.unlink(missing_ok=True)


def _reason(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    said = text[-1] if text else type(exc).__name__
    if "timed out" in said:
        return "The page took longer than 45 seconds to load."
    return said[:300]
