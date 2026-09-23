"""FastAPI application.

Composition root: the only module that knows about both the adapters and the
routers. Routers depend on `deploypro.deps`, which depends on adapters; nothing
runs the other way.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from deploypro.adapters import db
from deploypro.config import get_settings
from deploypro.domain.errors import DeployProError
from deploypro.routers import deployments, github, health, processes, projects, webhooks
from deploypro.web import github as dashboard_github
from deploypro.web import routes as dashboard

logger = logging.getLogger("deploypro")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await db.open_pool(
        settings.database_url,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
    )
    # Created here as well as by the worker: the API writes router config the
    # moment a domain is verified, and a missing directory would make that the
    # first thing to fail rather than something noticed at startup.
    settings.router_config_dir.mkdir(parents=True, exist_ok=True)
    settings.build_root.mkdir(parents=True, exist_ok=True)
    logger.info(
        "deploypro control plane ready — deployments at *.%s over %s",
        settings.deploy_domain,
        settings.scheme,
    )
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(
    title="DeployPro",
    version="0.1.0",
    summary="A self-hosted deployment platform",
    lifespan=lifespan,
)

app.mount(
    "/static",
    StaticFiles(directory=str(dashboard.HERE / "static")),
    name="static",
)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(deployments.project_router)
app.include_router(deployments.router)
app.include_router(processes.project_router)
app.include_router(processes.router)
app.include_router(webhooks.router)
app.include_router(github.router)
# Before the dashboard, whose routes are the least specific.
app.include_router(dashboard_github.router)
# Last, because it owns the root path and its routes are the least specific.
app.include_router(dashboard.router)


@app.exception_handler(dashboard.NeedsLogin)
async def handle_needs_login(request: Request, exc: Exception) -> RedirectResponse:
    """A browser gets the sign-in page; only the API gets a 401.

    Sending JSON to someone who typed a URL is the kind of thing that makes a
    dashboard feel broken when it is merely locked.

    The page asked for comes along, so signing in lands on it rather than on
    the project list: that matters most when GitHub has just sent the browser
    back with an installation to record.
    """
    wanted = request.url.path
    if request.method == "GET" and wanted != "/":
        if request.url.query:
            wanted += "?" + request.url.query
        return RedirectResponse(f"/login?next={quote(wanted)}", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(DeployProError)
async def handle_deploypro_error(request: Request, exc: DeployProError) -> JSONResponse:
    """Every deliberate error becomes its own status code and a message
    written for the person reading it, not a stack trace."""
    if exc.status_code >= 500:
        logger.exception("unhandled: %s", exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )
