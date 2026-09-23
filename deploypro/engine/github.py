"""The GitHub App: connecting it, listing what it can see, importing a
repository, and the short-lived tokens git clones with.

How it fits together:

1. The owner presses "Connect GitHub". The dashboard posts a manifest (see
   deploypro.domain.github) to GitHub, GitHub creates the app under their
   account and sends the browser back with a one-time code, and
   `complete_manifest` swaps that code for the app's id, private key and
   webhook secret.
2. The owner installs the app on their account (or an organisation) and
   picks which repositories it may read. GitHub sends the browser back with
   the installation's id, and `record_installation` stores it, after asking
   GitHub, as the app, whether that installation really is this app's.
3. From then on the app lists repositories, reads private ones with a token
   that lasts an hour, and receives every push to them on one webhook.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from deploypro.adapters import crypto
from deploypro.adapters import github as api
from deploypro.config import Settings
from deploypro.domain import github as gh
from deploypro.domain import naming
from deploypro.domain.errors import Conflict, InvalidRequest, NotFound
from deploypro.domain.models import Project
from deploypro.repositories import github as github_repo
from deploypro.repositories import projects as project_repo
from deploypro.repositories.github import App, Installation

logger = logging.getLogger("deploypro.github")


async def get_app() -> App | None:
    return await github_repo.get_app()


async def require_app() -> App:
    app = await github_repo.get_app()
    if app is None:
        raise NotFound(
            "No GitHub App is connected. Connect one from the dashboard: "
            "New project → Connect GitHub."
        )
    return app


def webhook_secret(app: App, settings: Settings) -> str:
    return crypto.decrypt(app.webhook_secret_encrypted, key=settings.master_key)


def _jwt(app: App, settings: Settings) -> str:
    private_key = crypto.decrypt(app.private_key_encrypted, key=settings.master_key)
    return gh.app_jwt(app.app_id, private_key)


def install_url(app: App) -> str:
    """Where the owner installs the app, or changes which repositories an
    existing installation covers."""
    return f"{app.html_url}/installations/new"


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------


async def complete_manifest(code: str, settings: Settings) -> App:
    """Store the app GitHub just created from our manifest."""
    if await github_repo.get_app() is not None:
        raise Conflict(
            "A GitHub App is already connected. Disconnect it first if you "
            "mean to replace it."
        )
    data = await api.convert_manifest(settings.github_api_url, code)
    await github_repo.save_app(
        app_id=int(data["id"]),
        slug=data["slug"],
        name=data.get("name") or data["slug"],
        html_url=data.get("html_url") or f"{settings.github_url}/apps/{data['slug']}",
        owner_login=(data.get("owner") or {}).get("login", ""),
        private_key_encrypted=crypto.encrypt(data["pem"], key=settings.master_key),
        webhook_secret_encrypted=crypto.encrypt(
            data["webhook_secret"], key=settings.master_key
        ),
    )
    app = await require_app()
    logger.info("GitHub App %s (id %s) connected", app.slug, app.app_id)
    return app


async def record_installation(installation_id: int, settings: Settings) -> Installation:
    """Remember an installation, once GitHub confirms it is this app's.

    The id arrives in a URL anyone could type. Fetching it with the app's own
    JWT is what proves it: GitHub answers only for the app's installations.
    """
    app = await require_app()
    data = await api.get_installation(
        settings.github_api_url, _jwt(app, settings), installation_id
    )
    account = data.get("account") or {}
    installation = Installation(
        id=int(data["id"]),
        account_login=account.get("login", ""),
        account_type=account.get("type", "User"),
    )
    await github_repo.save_installation(
        installation.id, installation.account_login, installation.account_type
    )
    return installation


async def disconnect() -> None:
    _TOKENS.clear()
    await github_repo.forget_app()


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

#: Installation tokens by (installation, repository or None for all). Kept in
#: the process, never stored: each lasts an hour and is cheap to replace.
_TOKENS: dict[tuple[int, str | None], tuple[str, datetime]] = {}


async def installation_token(
    settings: Settings, installation_id: int, repo_name: str | None = None
) -> str:
    """A token for an installation, reused until it is close to expiring.

    With `repo_name` (the name without the owner), the token reads only that
    repository's contents. That is the kind git is given.
    """
    key = (installation_id, repo_name)
    cached = _TOKENS.get(key)
    margin = timedelta(seconds=gh.TOKEN_MARGIN_SECONDS)
    if cached and cached[1] - margin > datetime.now(UTC):
        return cached[0]

    app = await require_app()
    token, expires = await api.create_token(
        settings.github_api_url,
        _jwt(app, settings),
        installation_id,
        repositories=[repo_name] if repo_name else None,
    )
    _TOKENS[key] = (token, expires)
    return token


async def git_env(project: Project, settings: Settings) -> dict[str, str]:
    """What git needs to read this project's repository through the app."""
    assert project.github_installation_id is not None and project.github_repo
    name = project.github_repo.split("/", 1)[1]
    token = await installation_token(settings, project.github_installation_id, name)
    return gh.git_auth_env(project.repo_url, token)


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Repository:
    full_name: str
    private: bool
    default_branch: str
    pushed_at: str
    html_url: str
    installation_id: int
    #: The slug of the project already made from it, if there is one.
    project_slug: str | None = None

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]


async def list_repositories(settings: Settings) -> list[Repository]:
    """Every repository the app can read, most recently pushed first."""
    imported: dict[str, str] = {}
    for project in await project_repo.list_all():
        full = project.github_repo or gh.repo_from_url(
            project.repo_url, github_url=settings.github_url
        )
        if full:
            imported.setdefault(full.lower(), project.slug)

    found: list[Repository] = []
    for installation in await github_repo.list_installations():
        token = await installation_token(settings, installation.id)
        for repo in await api.list_repos(settings.github_api_url, token):
            found.append(
                Repository(
                    full_name=repo["full_name"],
                    private=bool(repo.get("private")),
                    default_branch=repo.get("default_branch") or "main",
                    pushed_at=repo.get("pushed_at") or "",
                    html_url=repo.get("html_url") or "",
                    installation_id=installation.id,
                    project_slug=imported.get(repo["full_name"].lower()),
                )
            )
    found.sort(key=lambda r: r.pushed_at, reverse=True)
    return found


async def find_repository(
    settings: Settings, installation_id: int, full_name: str
) -> Repository:
    """One repository, confirmed readable by the given installation."""
    if not gh.is_full_name(full_name):
        raise InvalidRequest(f"{full_name!r} is not a repository name like owner/name")
    installations = {i.id for i in await github_repo.list_installations()}
    if installation_id not in installations:
        raise NotFound("That GitHub installation is not connected to DeployPro")
    token = await installation_token(settings, installation_id)
    data = await api.get_repo(settings.github_api_url, token, full_name)
    return Repository(
        full_name=data["full_name"],
        private=bool(data.get("private")),
        default_branch=data.get("default_branch") or "main",
        pushed_at=data.get("pushed_at") or "",
        html_url=data.get("html_url") or "",
        installation_id=installation_id,
    )


async def import_repository(
    settings: Settings,
    *,
    installation_id: int,
    full_name: str,
    name: str = "",
    slug: str = "",
    branch: str = "",
    root_directory: str = "",
    memory_mb: int = 512,
) -> Project:
    """Make a project from a repository the app can read, linked to it."""
    repo = await find_repository(settings, installation_id, full_name)
    project = await project_repo.create(
        slug=slug.strip() or naming.slugify(name or repo.name),
        name=name.strip() or repo.name,
        repo_url=gh.clone_url(repo.full_name, github_url=settings.github_url),
        production_branch=branch.strip() or repo.default_branch,
        root_directory=root_directory.strip(),
        memory_mb=memory_mb,
    )
    return await github_repo.link_project(project.id, installation_id, repo.full_name)


async def link(project: Project, settings: Settings) -> Project:
    """Have the app read an existing project's repository from now on.

    Afterwards a push deploys without a webhook of the project's own, and a
    private repository is read without its deploy key. An SSH repository URL
    is switched to https, which is what the app's token works over.
    """
    full_name = project.github_repo or gh.repo_from_url(
        project.repo_url, github_url=settings.github_url
    )
    if not full_name:
        raise InvalidRequest(
            f"{project.repo_url} is not a repository on {settings.github_url}, so "
            "the GitHub App cannot read it."
        )
    app = await require_app()
    installation = await api.repo_installation(
        settings.github_api_url, _jwt(app, settings), full_name
    )
    if installation is None:
        raise InvalidRequest(
            f"The GitHub App cannot see {full_name}. Add the repository to the "
            f"app's installation: {install_url(app)}"
        )
    account = installation.get("account") or {}
    await github_repo.save_installation(
        int(installation["id"]), account.get("login", ""), account.get("type", "User")
    )
    https_url = gh.clone_url(full_name, github_url=settings.github_url)
    if project.repo_url != https_url:
        project = await project_repo.update(project.id, {"repo_url": https_url})
    return await github_repo.link_project(project.id, int(installation["id"]), full_name)


async def try_link(project: Project, settings: Settings) -> Project:
    """`link`, quietly: for a project just created from a URL, link it when
    the app can read it, and otherwise leave it exactly as it was."""
    if project.github_installation_id is not None:
        return project
    if not gh.repo_from_url(project.repo_url, github_url=settings.github_url):
        return project
    if await github_repo.get_app() is None:
        return project
    try:
        return await link(project, settings)
    except Exception as exc:  # noqa: BLE001 - linking is a bonus, never a failure
        logger.info("did not link %s to the GitHub App: %s", project.slug, exc)
        return project
