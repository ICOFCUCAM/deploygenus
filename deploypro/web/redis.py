"""The dashboard's Redis page: Configuration → Redis.

Adding, resizing, restarting and removing a project's built-in Redis. The URL
with its password is shown only on an explicit request, in the response to
that POST, never in a query string or a log. docs/design/plan-replace-render.md §3.1.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from deploypro.adapters import containers
from deploypro.deps import SettingsDep
from deploypro.domain import redis as rules
from deploypro.domain.errors import DeployProError
from deploypro.domain.models import EnvTarget
from deploypro.engine import redis as redis_engine
from deploypro.repositories import projects as project_repo
from deploypro.repositories import redis as redis_repo
from deploypro.web.pages import _project_page
from deploypro.web.routes import _redirect, signed_in

router = APIRouter(include_in_schema=False)


def _page(slug: str) -> str:
    return f"/projects/{slug}/config/redis"


@router.get("/projects/{slug}/config/redis", response_class=HTMLResponse)
async def redis_page(
    request: Request, slug: str, settings: SettingsDep, ok: str = "", err: str = ""
):
    signed_in(request)
    return await _render_page(request, slug, settings, ok=ok, err=err)


async def _render_page(request, slug, settings, *, ok="", err="", reveal=""):
    project = await project_repo.get_by_slug(slug)
    config = await redis_repo.get(project.id)
    status = None
    kept = False
    overridden: list[str] = []
    if config is not None:
        try:
            status = await redis_engine.status(project, config)
        except containers.DockerError:
            status = redis_engine.Status(state="unknown", error="Docker did not answer")
        # A variable the owner set under Environment wins over DeployPro's.
        own = {
            var.key
            for var in await project_repo.list_env(project.id)
            if var.target.covers(EnvTarget.PRODUCTION)
        }
        overridden = [name for name in config.env_names if name in own]
    else:
        try:
            kept = await containers.volume_exists(redis_engine.volume_name(project))
        except containers.DockerError:
            kept = False
    return await _project_page(
        request,
        "config_redis.html",
        project,
        "redis",
        {
            "config": config,
            "redis_status": status,
            "kept": kept,
            "overridden": overridden,
            "policies": rules.POLICIES,
            "defaults": {
                "memory_mb": rules.DEFAULT_MEMORY_MB,
                "policy": rules.DEFAULT_POLICY,
                "env_names": rules.DEFAULT_ENV_NAME,
            },
            "limits": (rules.MIN_MEMORY_MB, rules.MAX_MEMORY_MB),
            "container": rules.container_name(project.slug),
            "volume": redis_engine.volume_name(project),
            "warn_percent": rules.WARN_PERCENT,
            "reveal": reveal,
            "backups": settings.backup_dir is not None,
            "ok": ok,
            "err": err,
        },
    )


@router.post("/projects/{slug}/redis")
async def add_redis(
    request: Request,
    slug: str,
    settings: SettingsDep,
    memory_mb: Annotated[str, Form()] = str(rules.DEFAULT_MEMORY_MB),
    policy: Annotated[str, Form()] = rules.DEFAULT_POLICY,
    env_names: Annotated[str, Form()] = rules.DEFAULT_ENV_NAME,
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    try:
        config = await redis_engine.enable(
            project,
            memory_mb=rules.validate_memory(memory_mb),
            policy=rules.validate_policy(policy),
            env_names=rules.parse_env_names(env_names),
            settings=settings,
        )
    except DeployProError as exc:
        return _redirect(_page(slug), err=exc.message)
    except containers.DockerError:
        return _redirect(
            _page(slug),
            err="Redis is configured but its container did not start. DeployPro "
            "retries every minute; `docker logs "
            f"{rules.container_name(slug)}` says why.",
        )
    names = " and ".join(config.env_names)
    return _redirect(
        _page(slug),
        ok=f"Redis is running. Production gets {names} from its next deploy; "
        "redeploy to give it to what is running now.",
    )


@router.post("/projects/{slug}/redis/settings")
async def change_redis(
    request: Request,
    slug: str,
    settings: SettingsDep,
    memory_mb: Annotated[str, Form()],
    policy: Annotated[str, Form()],
    env_names: Annotated[str, Form()],
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    try:
        before = await redis_repo.get(project.id)
        changes = {
            "memory_mb": rules.validate_memory(memory_mb),
            "policy": rules.validate_policy(policy),
            "env_names": rules.parse_env_names(env_names),
        }
        config = await redis_engine.update(project, changes, settings=settings)
    except DeployProError as exc:
        return _redirect(_page(slug), err=exc.message)
    except containers.DockerError:
        return _redirect(_page(slug), err="Saved, but the container did not restart.")
    notes = []
    if before and (before.memory_mb, before.policy) != (config.memory_mb, config.policy):
        notes.append("Redis restarted with the new settings; its data was kept.")
    if before and before.env_names != config.env_names:
        notes.append("The new variable names reach the app at its next deploy.")
    return _redirect(_page(slug), ok=" ".join(notes) or "Nothing changed.")


@router.post("/projects/{slug}/redis/restart")
async def restart_redis(request: Request, slug: str, settings: SettingsDep):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    try:
        await redis_engine.restart(project, settings=settings)
    except DeployProError as exc:
        return _redirect(_page(slug), err=exc.message)
    except containers.DockerError:
        return _redirect(_page(slug), err="Docker did not restart it.")
    return _redirect(_page(slug), ok="Redis restarted. Its data was kept.")


@router.post("/projects/{slug}/redis/delete")
async def remove_redis(
    request: Request,
    slug: str,
    delete_data: Annotated[str, Form()] = "",
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    try:
        deleted = await redis_engine.disable(project, delete_data=delete_data == "1")
    except DeployProError as exc:
        return _redirect(_page(slug), err=exc.message)
    return _redirect(
        _page(slug),
        ok="Redis removed and its data deleted."
        if deleted
        else "Redis removed. Its data is kept, and comes back if you add Redis again. "
        "Redeploy so the app stops looking for it.",
    )


@router.post("/projects/{slug}/redis/url", response_class=HTMLResponse)
async def show_redis_url(request: Request, slug: str, settings: SettingsDep):
    """The full URL, password included, in this response only — for pasting
    into another project's Environment (plan §3.7)."""
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    config = await redis_repo.get(project.id)
    if config is None:
        return _redirect(_page(slug), err="This project has no Redis.")
    reveal = rules.url(project.slug, redis_engine.password(config, settings=settings))
    response = await _render_page(request, slug, settings, reveal=reveal)
    response.headers["Cache-Control"] = "no-store"
    return response
