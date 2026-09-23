"""The GitHub App's webhook: every push to every repository it can read.

One endpoint for all of them, signed with the app's own secret. A push is
matched to projects by the repository's `owner/name`, so importing a
repository is all it takes for its pushes to deploy; nobody adds a webhook
to anything.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, Request, status

from deploypro.deps import SettingsDep
from deploypro.domain.errors import DeployProError, InvalidRequest, NotFound
from deploypro.engine import github
from deploypro.repositories import github as github_repo
from deploypro.routers.webhooks import MAX_BODY_BYTES, _handle_push, _verify_signature

logger = logging.getLogger("deploypro.github")

router = APIRouter(prefix="/github", tags=["webhooks"])


@router.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def app_webhook(
    request: Request,
    settings: SettingsDep,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict:
    app = await github.get_app()
    if app is None:
        raise NotFound("No GitHub App is connected to this DeployPro")

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise InvalidRequest("Webhook payload is too large")
    _verify_signature(body, x_hub_signature_256, github.webhook_secret(app, settings))

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise InvalidRequest("The webhook body is not JSON") from exc

    match x_github_event:
        case "ping":
            return {"ignored": "ping — the app's webhook is wired up correctly"}
        case "installation":
            return await _installation(payload)
        case "push":
            return await _push(payload, settings)
        case _:
            return {"ignored": f"{x_github_event} events are not acted on"}


async def _installation(payload: dict) -> dict:
    """Keep the list of installations in step when one is added on GitHub
    rather than through the dashboard, or removed."""
    action = payload.get("action")
    installation = payload.get("installation") or {}
    account = installation.get("account") or {}
    if not installation.get("id"):
        return {"ignored": "no installation in the event"}
    if action == "deleted":
        await github_repo.delete_installation(int(installation["id"]))
        logger.info("GitHub App uninstalled from %s", account.get("login"))
        return {"removed": account.get("login")}
    if action in {"created", "new_permissions_accepted", "unsuspend"}:
        await github_repo.save_installation(
            int(installation["id"]), account.get("login", ""), account.get("type", "User")
        )
        return {"saved": account.get("login")}
    return {"ignored": f"installation {action}"}


async def _push(payload: dict, settings) -> dict:
    full_name = (payload.get("repository") or {}).get("full_name") or ""
    projects = await github_repo.projects_for_repo(full_name)
    if not projects:
        return {"ignored": f"no project is linked to {full_name}"}

    results = []
    for project in projects:
        try:
            outcome = await _handle_push(project, payload, settings)
        except DeployProError as exc:
            # One project failing to queue must not stop the others that
            # share the repository.
            logger.warning(
                "push to %s not queued for %s: %s", full_name, project.slug, exc
            )
            results.append({"project": project.slug, "error": exc.message})
            continue
        entry = {"project": project.slug}
        if outcome.ignored:
            entry["ignored"] = outcome.ignored
        elif outcome.deployment is not None:
            entry["deployment"] = outcome.deployment.short_id
        results.append(entry)
    return {"repository": full_name, "projects": results}
