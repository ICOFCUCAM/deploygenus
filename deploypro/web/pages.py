"""The dashboard's pages: one URL per page family (docs/design, Phase 2 §2B
and Phase 5).

Each handler loads the rows its page needs and hands them to
`deploypro.web.views`, which decides what they mean. The forms these pages
post to live in `deploypro.web.routes` and return here.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from deploypro.config import Settings
from deploypro.deps import SettingsDep
from deploypro.domain.github import repo_from_url
from deploypro.domain.models import Deployment, EnvTarget, Process, ProcessType, Project
from deploypro.domain.repo_url import is_ssh_url
from deploypro.domain.storage import docker_volume_name
from deploypro.engine import processes as process_engine
from deploypro.repositories import deployments as deployment_repo
from deploypro.repositories import github as github_repo
from deploypro.repositories import processes as process_repo
from deploypro.repositories import projects as project_repo
from deploypro.repositories import volumes as volume_repo
from deploypro.web import views
from deploypro.web.routes import describe_schedule, render, signed_in

router = APIRouter(include_in_schema=False)

RECENT = 25


async def _production(project: Project) -> Deployment | None:
    if project.production_deployment_id is None:
        return None
    return await deployment_repo.get(project.production_deployment_id)


async def _jobs_with_last_runs(processes: list[Process]):
    return [(p, await process_repo.last_run(p.id)) for p in views.jobs(processes)]


def _primary_host(domains, has_production: bool) -> str | None:
    """The project's address: a verified domain, and only once something
    serves it. Before the first deploy a verified hostname answers with the
    router's default page, which reads as "the site is broken"."""
    if not has_production:
        return None
    verified = [d for d in domains if d.is_verified]
    for domain in verified:
        if domain.is_primary:
            return domain.host
    return verified[0].host if verified else None


def _project_page(
    request: Request, template: str, project: Project, section: str, context: dict
):
    return render(request, template, {"project": project, "section": section, **context})


# ---------------------------------------------------------------------------
# Projects (home)
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def projects_page(
    request: Request, settings: SettingsDep, ok: str = "", err: str = ""
):
    signed_in(request)
    records = []
    all_deployments: list[tuple[Project, Deployment]] = []
    all_runs = []
    in_progress = False
    for project in await project_repo.list_all():
        production = await _production(project)
        deployments = await deployment_repo.list_for_project(project.id, limit=10)
        processes = await process_repo.list_for_project(project.id)
        jobs = await _jobs_with_last_runs(processes)
        domains = await project_repo.list_domains(project.id)
        found = views.conditions(project, production, deployments, jobs)
        latest = deployments[0] if deployments else None
        show_latest = (
            latest is not None
            and (production is None or latest.id != production.id)
            and latest.status is not views.DeploymentStatus.READY
        )
        records.append(
            {
                "project": project,
                "mark": views.project_mark(project, found),
                "needs_attention": bool(found),
                "reason": found[0] if found else None,
                "address": _primary_host(domains, production is not None),
                "production": production,
                "latest": views.view(latest, project) if show_latest else None,
                "workers": len(views.workers(processes)),
                "jobs": views.jobs_summary(jobs),
                "waiting_domains": [d for d in domains if not d.is_verified],
                "sort_at": latest.created_at if latest else project.created_at,
            }
        )
        all_deployments.extend((project, d) for d in deployments)
        for process, _last in jobs:
            for run in await process_repo.list_runs(process.id, limit=5):
                all_runs.append((project, process, run))
        in_progress = in_progress or views.anything_in_progress(
            deployments, (run for _p, run in jobs)
        )
    records.sort(key=lambda r: r["sort_at"], reverse=True)
    records.sort(key=lambda r: not r["needs_attention"])
    return render(
        request,
        "projects.html",
        {
            "records": records,
            "activity": views.activity(all_deployments, all_runs),
            "refresh": in_progress,
            "github_app": await github_repo.get_app(),
            "ok": ok,
            "err": err,
        },
    )


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("/projects/{slug}", response_class=HTMLResponse)
async def overview_page(
    request: Request, slug: str, settings: SettingsDep, ok: str = "", err: str = ""
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    production = await _production(project)
    deployments = await deployment_repo.list_for_project(project.id, limit=RECENT)
    processes = await process_repo.list_for_project(project.id)
    jobs = await _jobs_with_last_runs(processes)
    domains = await project_repo.list_domains(project.id)
    found = views.conditions(project, production, deployments, jobs)
    primary_host = _primary_host(domains, production is not None)
    return _project_page(
        request,
        "overview.html",
        project,
        "overview",
        {
            "production": production,
            "production_view": views.view(production, project, with_line=True)
            if production
            else None,
            "block": views.production_block(project, production, deployments),
            "conditions": found,
            "mark": views.project_mark(project, found),
            "address": primary_host,
            "production_url": settings.deployment_url(production.short_id)
            if production
            else "",
            "recent": [views.view(d, project) for d in deployments[:5]],
            "workers": [
                (p, *views.worker_state(p, production)) for p in views.workers(processes)
            ],
            "jobs": [(p, run, views.job_state(p, run), _next_run(p)) for p, run in jobs],
            "domains": [
                (
                    d,
                    views.domain_mark(
                        d,
                        has_production=production is not None,
                        primary_host=primary_host,
                    ),
                )
                for d in domains
            ],
            "refresh": views.anything_in_progress(deployments, (r for _p, r in jobs)),
            "ok": ok,
            "err": err,
        },
    )


def _next_run(process: Process):
    return process_engine.next_due(process) if process.enabled else None


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


@router.get("/projects/{slug}/deployments", response_class=HTMLResponse)
async def deployments_page(request: Request, slug: str, ok: str = "", err: str = ""):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    deployments = await deployment_repo.list_for_project(project.id, limit=RECENT)
    return _project_page(
        request,
        "deployments.html",
        project,
        "deployments",
        {
            "rows": [views.view(d, project) for d in deployments],
            "full": len(deployments) >= RECENT,
            "deploy": views.deploy_action(project, deployments),
            "ok": ok,
            "err": err,
        },
    )


@router.get("/deployments/{short_id}", response_class=HTMLResponse)
async def deployment_page(
    request: Request, short_id: str, settings: SettingsDep, ok: str = "", err: str = ""
):
    signed_in(request)
    deployment = await deployment_repo.get_by_short_id(short_id)
    project = await project_repo.get(deployment.project_id)
    production = await _production(project)
    logs = await deployment_repo.read_logs(deployment.id, limit=4000)
    v = views.view(deployment, project, with_line=True)
    return _project_page(
        request,
        "deployment.html",
        project,
        "deployments",
        {
            "v": v,
            "deployment": deployment,
            "actions": views.detail_actions(deployment, project, production),
            "sentence": views.detail_sentence(v),
            "waiting_behind": await _building_elsewhere(deployment)
            if deployment.status is views.DeploymentStatus.QUEUED
            else None,
            "logs": logs,
            "cursor": logs[-1].seq if logs else 0,
            "url": settings.deployment_url(deployment.short_id),
            "ok": ok,
            "err": err,
        },
    )


async def _building_elsewhere(queued: Deployment) -> tuple[Project, Deployment] | None:
    """The build a queued deployment is waiting behind, if any: one build runs
    at a time on this server (Phase 3 §5.1, S-QUEUED)."""
    oldest: tuple[Project, Deployment] | None = None
    for project in await project_repo.list_all():
        for d in await deployment_repo.list_for_project(project.id, limit=5):
            if d.id != queued.id and d.status in (
                views.DeploymentStatus.BUILDING,
                views.DeploymentStatus.DEPLOYING,
            ):
                started = d.started_at or d.created_at
                if oldest is None or started < (
                    oldest[1].started_at or oldest[1].created_at
                ):
                    oldest = (project, d)
    return oldest


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


async def _runtime_context(project: Project):
    production = await _production(project)
    processes = await process_repo.list_for_project(project.id)
    return production, processes


@router.get("/projects/{slug}/runtime", response_class=HTMLResponse)
async def runtime_page(request: Request, slug: str, settings: SettingsDep):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    production, processes = await _runtime_context(project)
    jobs = await _jobs_with_last_runs(processes)
    return _project_page(
        request,
        "runtime.html",
        project,
        "runtime",
        {
            "production": production,
            "production_view": views.view(production, project) if production else None,
            "workers": [
                (p, *views.worker_state(p, production)) for p in views.workers(processes)
            ],
            "jobs": [(p, run, views.job_state(p, run), _next_run(p)) for p, run in jobs],
        },
    )


@router.get("/projects/{slug}/runtime/workers", response_class=HTMLResponse)
async def workers_page(request: Request, slug: str, ok: str = "", err: str = ""):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    production, processes = await _runtime_context(project)
    return _project_page(
        request,
        "workers.html",
        project,
        "workers",
        {
            "production": production,
            "workers": [
                (p, *views.worker_state(p, production)) for p in views.workers(processes)
            ],
            "deploy": views.deploy_action(
                project, await deployment_repo.list_for_project(project.id, limit=5)
            ),
            "ok": ok,
            "err": err,
        },
    )


@router.get("/projects/{slug}/runtime/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request, slug: str, ok: str = "", err: str = ""):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    production, processes = await _runtime_context(project)
    jobs = await _jobs_with_last_runs(processes)
    return _project_page(
        request,
        "jobs.html",
        project,
        "jobs",
        {
            "production": production,
            "jobs": [
                (p, run, views.job_state(p, run), _next_run(p), describe_schedule(p))
                for p, run in jobs
            ],
            "refresh": views.anything_in_progress((), (r for _p, r in jobs)),
            "ok": ok,
            "err": err,
        },
    )


@router.get("/projects/{slug}/runtime/workers/{name}", response_class=HTMLResponse)
async def worker_page(
    request: Request, slug: str, name: str, ok: str = "", err: str = ""
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    process = await process_repo.get_by_name(project.id, name)
    if process.type is not ProcessType.WORKER:
        return RedirectResponse(f"/projects/{slug}/runtime/jobs/{name}", status_code=308)
    production = await _production(project)
    mark, sentence = views.worker_state(process, production)
    return _project_page(
        request,
        "worker.html",
        project,
        "workers",
        {"p": process, "mark": mark, "sentence": sentence, "ok": ok, "err": err},
    )


@router.get("/projects/{slug}/runtime/jobs/{name}", response_class=HTMLResponse)
async def job_page(request: Request, slug: str, name: str, ok: str = "", err: str = ""):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    process = await process_repo.get_by_name(project.id, name)
    if process.type is not ProcessType.CRON:
        return RedirectResponse(
            f"/projects/{slug}/runtime/workers/{name}", status_code=308
        )
    runs = await process_repo.list_runs(process.id, limit=25)
    return _project_page(
        request,
        "job.html",
        project,
        "jobs",
        {
            "p": process,
            "mark": views.job_state(process, runs[0] if runs else None),
            "description": describe_schedule(process),
            "next_run": _next_run(process),
            "runs": runs,
            "run_marks": views.JOB_MARKS,
            "refresh": views.anything_in_progress((), runs[:1]),
            "ok": ok,
            "err": err,
        },
    )


@router.get("/projects/{slug}/processes/{name}")
async def old_process_page(request: Request, slug: str, name: str):
    """The pre-redesign address, still printed by older alerts and CLI output."""
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    process = await process_repo.get_by_name(project.id, name)
    kind = "jobs" if process.type is ProcessType.CRON else "workers"
    return RedirectResponse(f"/projects/{slug}/runtime/{kind}/{name}", status_code=301)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@router.get("/projects/{slug}/config", response_class=HTMLResponse)
async def config_page(
    request: Request, slug: str, settings: SettingsDep, ok: str = "", err: str = ""
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    production = await _production(project)
    variables = await project_repo.list_env(project.id)
    domains = await project_repo.list_domains(project.id)
    volumes = await volume_repo.list_for_project(project.id)
    return _project_page(
        request,
        "config.html",
        project,
        "config",
        {
            "variables": variables,
            "changed": views.changed_since_production(variables, production),
            "domains": domains,
            "waiting": [d for d in domains if not d.is_verified],
            "volumes": volumes,
            "backups": settings.backup_dir is not None,
            "framework": _framework(project, production),
            "ok": ok,
            "err": err,
        },
    )


def _framework(project: Project, production: Deployment | None) -> str:
    if project.framework:
        return f"{project.framework} (set here)"
    if production is not None and production.framework:
        return f"{production.framework} (detected)"
    return "detected on the first deploy"


@router.get("/projects/{slug}/config/environment", response_class=HTMLResponse)
async def environment_page(request: Request, slug: str, ok: str = "", err: str = ""):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    production = await _production(project)
    variables = await project_repo.list_env(project.id)
    scopes: dict[EnvTarget, list] = defaultdict(list)
    for variable in sorted(variables, key=lambda v: v.key):
        scopes[variable.target].append(variable)
    shared = {v.key for v in scopes[EnvTarget.ALL]}
    overridden = sorted(
        (v.key, v.target)
        for target in (EnvTarget.PRODUCTION, EnvTarget.PREVIEW)
        for v in scopes[target]
        if v.key in shared
    )
    return _project_page(
        request,
        "config_environment.html",
        project,
        "environment",
        {
            "scopes": [
                (EnvTarget.ALL, "All environments", scopes[EnvTarget.ALL]),
                (EnvTarget.PRODUCTION, "Production only", scopes[EnvTarget.PRODUCTION]),
                (EnvTarget.PREVIEW, "Preview only", scopes[EnvTarget.PREVIEW]),
            ],
            "any": bool(variables),
            "changed": views.changed_since_production(variables, production),
            "overridden": overridden,
            "production": production,
            "ok": ok,
            "err": err,
        },
    )


@router.get("/projects/{slug}/config/domains", response_class=HTMLResponse)
async def domains_page(
    request: Request, slug: str, settings: SettingsDep, ok: str = "", err: str = ""
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    production = await _production(project)
    domains = await project_repo.list_domains(project.id)
    primary_host = _primary_host(domains, production is not None)
    return _project_page(
        request,
        "config_domains.html",
        project,
        "domains",
        {
            "domains": [
                (
                    d,
                    views.domain_mark(
                        d,
                        has_production=production is not None,
                        primary_host=primary_host,
                    ),
                )
                for d in domains
            ],
            "deploy_domain": settings.deploy_domain,
            "ok": ok,
            "err": err,
        },
    )


@router.get("/projects/{slug}/config/storage", response_class=HTMLResponse)
async def storage_page(
    request: Request, slug: str, settings: SettingsDep, ok: str = "", err: str = ""
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    return _project_page(
        request,
        "config_storage.html",
        project,
        "storage",
        {
            "volumes": [
                (volume, docker_volume_name(project.slug, volume.name))
                for volume in await volume_repo.list_for_project(project.id)
            ],
            "backups": settings.backup_dir is not None,
            "backup_hour": settings.backup_hour,
            "ok": ok,
            "err": err,
        },
    )


@router.get("/projects/{slug}/config/build", response_class=HTMLResponse)
async def build_page(request: Request, slug: str, ok: str = "", err: str = ""):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    production = await _production(project)
    return _project_page(
        request,
        "config_build.html",
        project,
        "build",
        {
            "detected": production.framework if production else None,
            "ok": ok,
            "err": err,
        },
    )


@router.get("/projects/{slug}/config/repository", response_class=HTMLResponse)
async def repository_page(
    request: Request, slug: str, settings: SettingsDep, ok: str = "", err: str = ""
):
    signed_in(request)
    project = await project_repo.get_by_slug(slug)
    return _project_page(
        request,
        "config_repository.html",
        project,
        "repository",
        {
            "github_app": await github_repo.get_app(),
            "repo_on_github": bool(
                repo_from_url(project.repo_url, github_url=settings.github_url)
            ),
            "repo_is_ssh": is_ssh_url(project.repo_url),
            "webhook_url": f"{settings.dashboard_url}/webhooks/{project.slug}",
            "ok": ok,
            "err": err,
        },
    )


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@router.get("/system", response_class=HTMLResponse)
async def system_page(
    request: Request, settings: SettingsDep, ok: str = "", err: str = ""
):
    signed_in(request)
    from deploypro.engine import github

    app = await github_repo.get_app()
    installations = await github_repo.list_installations() if app else []
    return render(
        request,
        "system.html",
        {
            "section": "system",
            "app": app,
            "installations": installations,
            "install_url": github.install_url(app) if app else "",
            "settings_rows": _settings_rows(settings),
            "ok": ok,
            "err": err,
        },
    )


def _settings_rows(settings: Settings) -> list[tuple[str, str, str]]:
    """The installation's settings, read-only (Phase 2 decision 2), each with
    the variable in /opt/deploypro/.env that changes it. Secrets say only
    whether they are set."""
    backups = (
        f"daily at {settings.backup_hour:02d}:00 UTC, keeping {settings.backup_keep}"
        if settings.backup_dir
        else "off"
    )
    return [
        ("Dashboard address", settings.dashboard_url, "DEPLOYPRO_DASHBOARD_DOMAIN"),
        ("Deployment domain", f"*.{settings.deploy_domain}", "DEPLOYPRO_DEPLOY_DOMAIN"),
        (
            "Alerts webhook",
            "set" if settings.alert_webhook_url else "not set",
            "DEPLOYPRO_ALERT_WEBHOOK_URL",
        ),
        ("Backups", backups, "DEPLOYPRO_BACKUP_DIR, DEPLOYPRO_BACKUP_HOUR"),
        ("Images kept per project", str(settings.keep_images), "DEPLOYPRO_KEEP_IMAGES"),
        (
            "Build logs kept",
            f"{settings.log_retention_days} days",
            "DEPLOYPRO_LOG_RETENTION_DAYS",
        ),
        (
            "Disk alert",
            f"at {settings.disk_alert_percent}% full",
            "DEPLOYPRO_DISK_ALERT_PERCENT",
        ),
    ]
