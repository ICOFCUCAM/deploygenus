"""The dashboard.

Server-rendered, and deliberately so. This is the page you open when a deploy
has gone wrong, so it has no build step, no separate deployment and no
JavaScript it needs in order to work — every action on it is a plain form that
posts and redirects. The only script on the page adds live log lines, and the
page is complete without it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from deploypro.adapters import crypto
from deploypro.config import Settings, get_settings
from deploypro.deps import SettingsDep, token_matches
from deploypro.domain import naming, session
from deploypro.domain.errors import DeployProError
from deploypro.domain.models import (
    Deployment,
    DeploymentStatus,
    Domain,
    EnvTarget,
    EnvVar,
    ProcessType,
    Project,
)
from deploypro.domain.repo_url import validate_repo_url
from deploypro.domain.schedule import InvalidSchedule, describe, parse
from deploypro.domain.storage import (
    docker_volume_name,
    normalise_mount_path,
    validate_volume_name,
)
from deploypro.engine import promote as promote_engine
from deploypro.engine import routing, service, verify
from deploypro.engine.logs import LogWriter
from deploypro.repositories import deployments as deployment_repo
from deploypro.repositories import processes as process_repo
from deploypro.repositories import projects as project_repo
from deploypro.repositories import volumes as volume_repo
from deploypro.web import views

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals.update(
    split_label=views.split_label,
    clock=views.clock,
    duration=views.duration,
    ago=views.ago,
)

router = APIRouter(include_in_schema=False)


class NeedsLogin(Exception):
    """Raised instead of 401 so a browser gets the sign-in page, not JSON."""


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


def current_session(request: Request, settings: Settings) -> None:
    cookie = request.cookies.get(session.COOKIE_NAME)
    if not cookie:
        raise NeedsLogin
    try:
        session.verify(cookie, token=settings.api_token)
    except session.InvalidSession as exc:
        raise NeedsLogin from exc


def signed_in(request: Request) -> None:
    current_session(request, get_settings())


def safe_next(target: str) -> str:
    """Where to go after signing in: a path on this dashboard, or home.

    Anything else ("//evil.example", "https://…") would make the sign-in page
    an open redirect: a link that shows this dashboard's login and then hands
    the freshly signed-in browser to someone else's site.
    """
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request, settings: SettingsDep, err: str = "", next: str = "/"
):
    return _render(
        request, "login.html", {"err": err, "next": safe_next(next)}, nav=False
    )


@router.post("/login")
async def login(
    request: Request,
    settings: SettingsDep,
    token: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
):
    if not token_matches(token.strip(), settings.api_token):
        target = "/login" if safe_next(next) == "/" else f"/login?next={quote(next)}"
        return _redirect(target, err="That token is not valid.")

    response = RedirectResponse(safe_next(next), status_code=303)
    response.set_cookie(
        session.COOKIE_NAME,
        session.issue(token=settings.api_token),
        max_age=session.MAX_AGE_SECONDS,
        httponly=True,
        # Lax rather than Strict: Strict would drop the cookie when arriving
        # from a link in a deploy notification, which is exactly the journey
        # this dashboard is opened by.
        samesite="lax",
        secure=settings.tls_enabled,
        path="/",
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(session.COOKIE_NAME, path="/")
    return response


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/projects/new", response_class=HTMLResponse)
async def new_project_form(
    request: Request, settings: SettingsDep, q: str = "", ok: str = "", err: str = ""
):
    signed_in(request)
    from deploypro.web.github import new_project_context

    context = await new_project_context(settings, q=q)
    return _render(request, "new_project.html", {**context, "ok": ok, "err": err})


@router.post("/projects/new")
async def create_project(
    request: Request,
    settings: SettingsDep,
    name: Annotated[str, Form()],
    repo_url: Annotated[str, Form()],
    production_branch: Annotated[str, Form()] = "main",
    root_directory: Annotated[str, Form()] = "",
    slug: Annotated[str, Form()] = "",
    memory_mb: Annotated[str, Form()] = "512",
):
    signed_in(request)
    try:
        validate_repo_url(repo_url)
        project = await project_repo.create(
            slug=slug.strip() or naming.slugify(name),
            name=name.strip(),
            repo_url=repo_url.strip(),
            production_branch=production_branch.strip() or "main",
            root_directory=root_directory.strip(),
            memory_mb=_int(memory_mb, 512),
        )
    except DeployProError as exc:
        return _redirect("/projects/new", err=exc.message)
    from deploypro.engine import github

    project = await github.try_link(project, settings)
    linked = " Linked to the GitHub App: pushes deploy it." if project.github_repo else ""
    return _redirect(f"/projects/{project.slug}", ok=f"Created {project.name}.{linked}")


@router.post("/projects/{slug}/deploy")
async def trigger_deploy(request: Request, slug: str, back: Annotated[str, Form()] = ""):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    try:
        deployment = await service.queue_deploy(project)
    except DeployProError as exc:
        return _redirect(_back(back, f"/projects/{slug}"), err=exc.message)
    return _redirect(f"/deployments/{deployment.short_id}")


@router.post("/projects/{slug}/deploy-key")
async def make_deploy_key(
    request: Request,
    slug: str,
    settings: SettingsDep,
    rotate: Annotated[str, Form()] = "",
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    from deploypro.engine import gitaccess

    await gitaccess.ensure_key(project, settings, rotate=bool(rotate))
    message = (
        "New deploy key made — replace the old one on GitHub."
        if rotate
        else ("Deploy key made — add it to the repository on GitHub.")
    )
    return _redirect(f"/projects/{slug}/config/repository", ok=message)


@router.post("/projects/{slug}/config/build")
async def save_build(
    request: Request,
    slug: str,
    root_directory: Annotated[str, Form()] = "",
    framework: Annotated[str, Form()] = "",
    install_command: Annotated[str, Form()] = "",
    build_command: Annotated[str, Form()] = "",
    start_command: Annotated[str, Form()] = "",
    port: Annotated[str, Form()] = "",
    memory_mb: Annotated[str, Form()] = "",
    cpu_shares: Annotated[str, Form()] = "",
    keep_warm: Annotated[str, Form()] = "",
    stop_timeout_seconds: Annotated[str, Form()] = "",
):
    """Configuration → Build. Every field on the page, and only those.

    The Repository page has its own handler: a form that does not carry a
    field must never be read as "clear it", which is what one shared handler
    did to the build overrides whenever a form without them was posted.
    """
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    back = f"/projects/{slug}/config/build"
    try:
        parsed_port = _optional_port(port)
        parsed_cpu = _cpu(cpu_shares, project.cpu_shares)
    except ValueError as exc:
        return _redirect(back, err=str(exc))
    changes: dict = {
        "root_directory": root_directory.strip(),
        "port": parsed_port,
        "memory_mb": max(_int(memory_mb, project.memory_mb), 64),
        "cpu_shares": parsed_cpu,
        "keep_warm": max(_int(keep_warm, project.keep_warm), 0),
        "stop_timeout_seconds": min(
            max(_int(stop_timeout_seconds, project.stop_timeout_seconds), 1), 86400
        ),
    }
    # An empty override means "go back to detecting it", which is a real
    # setting and not a missing field — so these are written as NULL.
    for field_name, value in (
        ("framework", framework),
        ("install_command", install_command),
        ("build_command", build_command),
        ("start_command", start_command),
    ):
        changes[field_name] = value.strip() or None

    await project_repo.update(project.id, changes)
    return _redirect(
        back,
        ok="Saved. Build settings and resources apply to the next deploy; the "
        "graceful shutdown time to the next replacement.",
    )


@router.post("/projects/{slug}/config/repository")
async def save_repository(
    request: Request,
    slug: str,
    name: Annotated[str, Form()] = "",
    production_branch: Annotated[str, Form()] = "",
):
    """Configuration → Repository: the project's name and production branch."""
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    await project_repo.update(
        project.id,
        {
            "name": name.strip() or project.name,
            "production_branch": production_branch.strip() or project.production_branch,
        },
    )
    return _redirect(
        f"/projects/{slug}/config/repository",
        ok="Saved. The production branch applies to the next push or deploy.",
    )


@router.post("/projects/{slug}/delete")
async def delete_project(
    request: Request,
    slug: str,
    settings: SettingsDep,
    confirm: Annotated[str, Form()] = "",
):
    """Deleting needs the project's name typed (Phase 3 Q-S5): it cannot be
    undone, and a stray click on a button must not be enough."""
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    if confirm.strip() != project.name:
        return _redirect(
            f"/projects/{slug}/config",
            err=f"Type the project name, {project.name}, exactly to delete it.",
        )
    await service.delete_project(project, settings)
    return _redirect("/", ok=f"Deleted {project.name}. Its files are kept.")


@router.post("/projects/{slug}/env")
async def add_env(
    request: Request,
    slug: str,
    settings: SettingsDep,
    key: Annotated[str, Form()],
    value: Annotated[str, Form()],
    target: Annotated[str, Form()] = "all",
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    cleaned = key.strip()
    if not cleaned.replace("_", "").isalnum() or cleaned[:1].isdigit():
        return _redirect(
            f"/projects/{slug}/config/environment",
            err=f"{cleaned!r} is not a usable variable name.",
        )
    await project_repo.set_env(
        project.id,
        cleaned,
        crypto.encrypt(value, key=settings.master_key),
        EnvTarget(target),
    )
    return _redirect(
        f"/projects/{slug}/config/environment",
        ok=f"Saved {cleaned}. It applies on the next deploy.",
    )


@router.post("/projects/{slug}/env/{key}/delete")
async def remove_env(request: Request, slug: str, key: str):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    await project_repo.delete_env(project.id, key, None)
    return _redirect(
        f"/projects/{slug}/config/environment",
        ok=f"Removed {key}. Redeploy to apply.",
    )


@router.post("/projects/{slug}/domains")
async def add_domain(
    request: Request,
    slug: str,
    settings: SettingsDep,
    host: Annotated[str, Form()],
    primary: Annotated[str, Form()] = "",
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    cleaned = host.strip().lower().rstrip(".")
    if "." not in cleaned or "/" in cleaned:
        return _redirect(
            f"/projects/{slug}/config/domains", err=f"{host!r} is not a hostname."
        )
    if cleaned.endswith(settings.deploy_domain):
        return _redirect(
            f"/projects/{slug}/config/domains",
            err=(
                f"{cleaned} is under the platform's own domain, which every "
                "deployment already uses."
            ),
        )
    try:
        await project_repo.add_domain(project.id, cleaned, primary=bool(primary))
    except DeployProError as exc:
        return _redirect(f"/projects/{slug}/config/domains", err=exc.message)
    return _redirect(
        f"/projects/{slug}/config/domains",
        ok=f"Added {cleaned}. Point its DNS here, then verify it.",
    )


@router.post("/projects/{slug}/volumes")
async def add_volume(
    request: Request,
    slug: str,
    name: Annotated[str, Form()],
    mount_path: Annotated[str, Form()],
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    try:
        volume = await volume_repo.create(
            project_id=project.id,
            name=validate_volume_name(name),
            mount_path=normalise_mount_path(mount_path),
        )
    except DeployProError as exc:
        return _redirect(f"/projects/{slug}/config/storage", err=exc.message)
    return _redirect(
        f"/projects/{slug}/config/storage",
        ok=f"Added {volume.name} at {volume.mount_path}. It is mounted from the "
        "next deploy.",
    )


@router.post("/projects/{slug}/volumes/{name}/delete")
async def remove_volume(request: Request, slug: str, name: str):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    try:
        volume = await volume_repo.delete(project.id, name)
    except DeployProError as exc:
        return _redirect(f"/projects/{slug}/config/storage", err=exc.message)
    return _redirect(
        f"/projects/{slug}/config/storage",
        ok=f"{volume.name} is no longer mounted from the next deploy. Its data is "
        f"kept in Docker volume {docker_volume_name(project.slug, volume.name)}.",
    )


@router.post("/projects/{slug}/domains/{host}/verify")
async def verify_domain(request: Request, slug: str, host: str, settings: SettingsDep):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    domains = {d.host: d for d in await project_repo.list_domains(project.id)}
    domain = domains.get(host.lower())
    if domain is None:
        return _redirect(
            f"/projects/{slug}/config/domains", err=f"{host} is not on this project."
        )

    result = await verify.verify(domain.host, expected_host=settings.deploy_domain)
    if not result.verified:
        return _redirect(f"/projects/{slug}/config/domains", err=result.detail)

    await project_repo.mark_domain_verified(domain.id)
    await routing.refresh(await project_repo.get(project.id), settings=settings)
    return _redirect(f"/projects/{slug}/config/domains", ok=result.detail)


@router.post("/projects/{slug}/domains/{host}/delete")
async def remove_domain(request: Request, slug: str, host: str, settings: SettingsDep):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    await project_repo.remove_domain(project.id, host)
    await routing.refresh(await project_repo.get(project.id), settings=settings)
    return _redirect(f"/projects/{slug}/config/domains", ok=f"Removed {host}.")


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------


@router.post("/projects/{slug}/processes")
async def add_process(
    request: Request,
    slug: str,
    name: Annotated[str, Form()],
    type: Annotated[str, Form()],
    command: Annotated[str, Form()] = "",
    schedule: Annotated[str, Form()] = "",
    memory_mb: Annotated[str, Form()] = "512",
    replicas: Annotated[str, Form()] = "1",
    timeout_seconds: Annotated[str, Form()] = "900",
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    kind = ProcessType(type)
    back = _process_list(slug, kind)

    cleaned_schedule: str | None = None
    if kind is ProcessType.CRON:
        try:
            cleaned_schedule = parse(schedule).expression
        except InvalidSchedule as exc:
            return _redirect(back, err=str(exc))

    try:
        await process_repo.create(
            project_id=project.id,
            name=name.strip(),
            type=kind,
            command=command.strip() or None,
            schedule=cleaned_schedule,
            memory_mb=_int(memory_mb, 512),
            replicas=_int(replicas, 1),
            timeout_seconds=_int(timeout_seconds, 900),
        )
    except DeployProError as exc:
        return _redirect(back, err=exc.message)

    note = (
        "It runs against whatever is serving production, never a preview."
        if kind is ProcessType.CRON
        else "It starts on the next deploy."
    )
    return _redirect(back, ok=f"Added {name.strip()}. {note}")


@router.post("/projects/{slug}/processes/{name}/toggle")
async def toggle_process(
    request: Request, slug: str, name: str, back: Annotated[str, Form()] = ""
):
    """Pause or resume.

    For a job this is immediate: the scheduler reads only enabled jobs. For a
    worker it is not: workers are reconciled when production changes, so the
    message says the next deploy (Phase 3 Q-S4) rather than claiming a
    running worker has stopped.
    """
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    process = await process_repo.get_by_name(project.id, name)
    await process_repo.update(process.id, {"enabled": not process.enabled})
    if process.type is ProcessType.CRON:
        message = (
            f"{name} paused: no runs are scheduled."
            if process.enabled
            else f"{name} resumed."
        )
    else:
        message = (
            f"{name} pauses at the next deploy. It keeps running until then."
            if process.enabled
            else f"{name} resumes at the next deploy."
        )
    return _redirect(_back(back, _process_list(slug, process.type)), ok=message)


@router.post("/projects/{slug}/processes/{name}/run")
async def run_process_now(request: Request, slug: str, name: str):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    process = await process_repo.get_by_name(project.id, name)
    page = f"/projects/{slug}/runtime/jobs/{name}"
    if process.type is not ProcessType.CRON:
        return _redirect(
            f"/projects/{slug}/runtime/workers/{name}",
            err=f"{name} runs continuously, so there is nothing to trigger.",
        )

    # The manual run takes the current minute's slot, so pressing this at
    # 02:59:58 on a job due at 03:00 produces one run rather than two.
    slot = datetime.now(UTC).replace(second=0, microsecond=0)
    run = await process_repo.claim_slot(
        process.id, slot, project.production_deployment_id
    )
    if run is None:
        return _redirect(page, err="Already queued or running for this minute.")
    return _redirect(page, ok="Queued. It starts within seconds.")


@router.post("/projects/{slug}/processes/{name}/delete")
async def delete_process(request: Request, slug: str, name: str):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    process = await process_repo.get_by_name(project.id, name)
    await process_repo.delete(process.id)
    return _redirect(_process_list(slug, process.type), ok=f"Removed {name}.")


def _process_list(slug: str, kind: ProcessType) -> str:
    return f"/projects/{slug}/runtime/{'jobs' if kind is ProcessType.CRON else 'workers'}"


def describe_schedule(process) -> str | None:
    """A job's schedule in words, or None for a worker."""
    if not process.runs_on_a_schedule:
        return None
    try:
        return describe(parse(process.schedule))
    except InvalidSchedule:
        return "unreadable schedule"


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


@router.post("/deployments/{short_id}/promote")
async def promote(request: Request, short_id: str, settings: SettingsDep):
    signed_in(request)
    deployment = await deployment_repo.get_by_short_id(short_id)
    project = await project_repo.get(deployment.project_id)
    previous = None
    if project.production_deployment_id:
        previous = await deployment_repo.get(project.production_deployment_id)
    log = await LogWriter.resume(deployment.id)
    try:
        await promote_engine.promote(
            project,
            deployment,
            settings=settings,
            log=log,
            workdir=settings.build_root / deployment.short_id,
        )
    except DeployProError as exc:
        return _redirect(
            f"/deployments/{short_id}", err=f"{exc.message} Production is unchanged."
        )
    finally:
        await log.flush()
    # Phase 3 §7: say where production is now, and that the way back is open.
    message = f"Production is now #{deployment.number} ({deployment.git_sha[:8]})."
    if previous is not None and previous.id != deployment.id:
        previous = await deployment_repo.get(previous.id)
        if previous.status is DeploymentStatus.READY:
            direction = "forward" if previous.number > deployment.number else "back"
            message += (
                f" #{previous.number} is still available: roll {direction} any time."
            )
    return _redirect(f"/projects/{project.slug}", ok=message)


@router.post("/deployments/{short_id}/redeploy")
async def redeploy(request: Request, short_id: str):
    signed_in(request)
    deployment = await deployment_repo.get_by_short_id(short_id)
    project = await project_repo.get(deployment.project_id)
    try:
        queued = await service.redeploy(project, deployment)
    except DeployProError as exc:
        return _redirect(f"/deployments/{short_id}", err=exc.message)
    return _redirect(f"/deployments/{queued.short_id}")


@router.post("/deployments/{short_id}/cancel")
async def cancel(request: Request, short_id: str):
    signed_in(request)
    deployment = await deployment_repo.get_by_short_id(short_id)
    if deployment.status.is_terminal or deployment.status.is_in_flight:
        return _redirect(
            f"/deployments/{short_id}",
            err=f"This deployment is {deployment.status.value} and cannot be cancelled.",
        )
    await deployment_repo.mark_cancelled(deployment.id)
    return _redirect(f"/deployments/{short_id}", ok="Cancelled.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def render(request: Request, template: str, context: dict, *, nav: bool = True):
    now = datetime.now(UTC)
    return templates.TemplateResponse(
        request, template, {"show_nav": nav, "now": now, **context}
    )


# The older name, still used by the GitHub pages.
_render = render


def _back(target: str, default: str) -> str:
    """Where a form returns to: the page it was posted from, when it says so
    and that page is on this dashboard, else the handler's own page."""
    return safe_next(target) if target and safe_next(target) != "/" else default


def _optional_port(raw: str) -> int | None:
    cleaned = raw.strip()
    if not cleaned:
        return None
    if not cleaned.isdigit() or not 0 < int(cleaned) < 65536:
        raise ValueError(f"{cleaned!r} is not a port number (1–65535).")
    return int(cleaned)


def _cpu(raw: str, fallback: float) -> float:
    cleaned = raw.strip()
    if not cleaned:
        return fallback
    try:
        value = float(cleaned)
    except ValueError:
        raise ValueError(f"{cleaned!r} is not a number of CPUs.") from None
    if not 0 < value <= 99:
        raise ValueError("CPU must be more than 0 and at most 99.")
    return round(value, 2)


def _redirect(path: str, *, ok: str = "", err: str = "") -> RedirectResponse:
    """Post-redirect-get, with the message carried in the query string.

    No flash storage: there is no server-side session to put one in, and
    adding one so a sentence can survive a redirect would be a table, a
    cleanup job and a shared-state problem in exchange for a tidier URL.
    """
    if ok:
        path += ("&" if "?" in path else "?") + "ok=" + quote(ok)
    elif err:
        path += ("&" if "?" in path else "?") + "err=" + quote(err)
    return RedirectResponse(path, status_code=303)


def _int(raw: str, fallback: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback


# Re-exported for the type checker's benefit; the templates use them by name.
__all__ = ["Deployment", "Domain", "EnvVar", "NeedsLogin", "Project", "router"]
