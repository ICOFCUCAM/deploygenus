"""The dashboard's GitHub pages: connecting the app, and importing a
repository with one click.

Kept apart from deploypro.web.routes because every page here talks to GitHub
and the rest of the dashboard never does. Same rules otherwise: plain forms,
post-redirect-get, no script needed.
"""

from __future__ import annotations

import json
import secrets
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from deploypro.deps import SettingsDep
from deploypro.domain import github as gh
from deploypro.domain.errors import DeployProError
from deploypro.domain.models import DeploymentTrigger
from deploypro.engine import github, service
from deploypro.repositories import github as github_repo
from deploypro.repositories import projects as project_repo
from deploypro.web.routes import _int, _redirect, _render, signed_in

router = APIRouter(include_in_schema=False)

#: Carries the manifest flow's `state` from the page that starts it to the
#: page GitHub sends the browser back to. Checked there, so a code minted for
#: somebody else's app cannot be slipped into this DeployPro by a link.
STATE_COOKIE = "deploypro_github_state"


@router.get("/github/connect", response_class=HTMLResponse)
async def connect_page(request: Request, settings: SettingsDep, org: str = ""):
    """The page that sends the owner to GitHub to create the app."""
    signed_in(request)
    if await github.get_app() is not None:
        return _redirect("/projects/new", err="A GitHub App is already connected.")

    # GitHub returns the browser to the dashboard's public address. Start
    # there too, or the state cookie (and the sign-in) would be on another
    # host and the return would be refused.
    home = urlsplit(settings.dashboard_url).hostname
    other = f"deploypro.{settings.deploy_domain}"
    if home != other and request.url.hostname == other:
        return RedirectResponse(
            f"{settings.dashboard_url}/github/connect", status_code=303
        )

    org = org.strip()
    if org and not gh.is_full_name(f"{org}/x"):
        return _redirect("/github/connect", err=f"{org!r} is not a GitHub account name.")
    state = secrets.token_urlsafe(24)
    target = (
        f"{settings.github_url}/organizations/{org}/settings/apps/new"
        if org
        else f"{settings.github_url}/settings/apps/new"
    )
    manifest = gh.manifest(
        dashboard_url=settings.dashboard_url, deploy_domain=settings.deploy_domain
    )
    response = _render(
        request,
        "github_connect.html",
        {
            "action": f"{target}?state={state}",
            "manifest": json.dumps(manifest),
            "app_name": manifest["name"],
            "org": org,
            "dashboard_url": settings.dashboard_url,
            "permissions": gh.PERMISSIONS,
            "err": request.query_params.get("err", ""),
        },
    )
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=3600,
        httponly=True,
        samesite="lax",
        secure=settings.tls_enabled,
        path="/github",
    )
    return response


@router.get("/github/created")
async def app_created(
    request: Request, settings: SettingsDep, code: str = "", state: str = ""
):
    """GitHub made the app. Swap the one-time code for it, then install it."""
    signed_in(request)
    expected = request.cookies.get(STATE_COOKIE, "")
    if (
        not code
        or not state
        or not expected
        or not secrets.compare_digest(state, expected)
    ):
        return _redirect(
            "/github/connect",
            err="That link did not come from this dashboard's Connect GitHub page. "
            "Start again from here.",
        )
    try:
        app = await github.complete_manifest(code, settings)
    except DeployProError as exc:
        return _redirect("/projects/new", err=exc.message)
    response = RedirectResponse(github.install_url(app), status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/github")
    return response


@router.get("/github/installed")
async def app_installed(
    request: Request,
    settings: SettingsDep,
    installation_id: str = "",
    setup_action: str = "",
):
    """GitHub installed the app (or changed which repositories it covers)."""
    signed_in(request)
    if setup_action == "request" or not installation_id.isdigit():
        return _redirect(
            "/projects/new",
            err="GitHub did not install the app. An organisation owner may need "
            "to approve the request.",
        )
    try:
        installation = await github.record_installation(int(installation_id), settings)
    except DeployProError as exc:
        return _redirect("/projects/new", err=exc.message)
    return _redirect(
        "/projects/new",
        ok=f"GitHub is connected: DeployPro can read the repositories you chose "
        f"on {installation.account_login}.",
    )


@router.post("/github/disconnect")
async def disconnect(request: Request):
    signed_in(request)
    await github.disconnect()
    return _redirect(
        "/projects/new",
        ok="Disconnected. Delete the app on GitHub too (Settings → Developer "
        "settings → GitHub Apps) so it stops sending events.",
    )


@router.get("/projects/new/github", response_class=HTMLResponse)
async def import_form(
    request: Request,
    settings: SettingsDep,
    repo: str = "",
    installation: str = "",
    err: str = "",
):
    signed_in(request)
    try:
        found = await github.find_repository(settings, _int(installation, 0), repo)
    except DeployProError as exc:
        return _redirect("/projects/new", err=exc.message)
    return _render(
        request,
        "import_project.html",
        {"repo": found, "suggested_name": found.name, "err": err},
    )


@router.post("/projects/new/github")
async def import_repository(
    request: Request,
    settings: SettingsDep,
    repo: Annotated[str, Form()],
    installation: Annotated[str, Form()],
    name: Annotated[str, Form()] = "",
    production_branch: Annotated[str, Form()] = "",
    root_directory: Annotated[str, Form()] = "",
    slug: Annotated[str, Form()] = "",
    memory_mb: Annotated[str, Form()] = "512",
):
    signed_in(request)
    back = "/projects/new/github?" + urlencode(
        {"repo": repo, "installation": installation}
    )
    try:
        project = await github.import_repository(
            settings,
            installation_id=_int(installation, 0),
            full_name=repo,
            name=name,
            slug=slug,
            branch=production_branch,
            root_directory=root_directory,
            memory_mb=_int(memory_mb, 512),
        )
    except DeployProError as exc:
        return _redirect(back, err=exc.message)

    # Deployed straight away, as it would be on a push: importing is the
    # decision to deploy it.
    try:
        deployment = await service.queue_deploy(project, trigger=DeploymentTrigger.MANUAL)
    except DeployProError as exc:
        return _redirect(f"/projects/{project.slug}", err=exc.message)
    return _redirect(f"/deployments/{deployment.short_id}")


@router.post("/projects/{slug}/github/link")
async def link_project(request: Request, slug: str, settings: SettingsDep):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    try:
        project = await github.link(project, settings)
    except DeployProError as exc:
        return _redirect(f"/projects/{slug}", err=exc.message)
    return _redirect(
        f"/projects/{slug}",
        ok=f"Linked to {project.github_repo} — every push now deploys by itself.",
    )


@router.post("/projects/{slug}/github/unlink")
async def unlink_project(request: Request, slug: str):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    await github_repo.unlink_project(project.id)
    return _redirect(
        f"/projects/{slug}",
        ok="Unlinked from the GitHub App. Pushes no longer deploy this project.",
    )


async def new_project_context(settings, q: str = "") -> dict:
    """What the New project page shows above the manual form."""
    app = await github.get_app()
    context: dict = {
        "app": app,
        "installations": [],
        "repos": [],
        "repo_error": "",
        "q": q,
    }
    if app is None:
        return context
    context["install_url"] = github.install_url(app)
    context["installations"] = await github_repo.list_installations()
    if context["installations"]:
        try:
            repos = await github.list_repositories(settings)
        except DeployProError as exc:
            context["repo_error"] = exc.message
            repos = []
        needle = q.strip().lower()
        if needle:
            repos = [r for r in repos if needle in r.full_name.lower()]
        context["repos"] = repos
    return context
