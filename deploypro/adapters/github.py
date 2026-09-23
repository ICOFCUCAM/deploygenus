"""GitHub's REST API, as far as the GitHub App needs it.

Every function takes the API's base URL, so GitHub Enterprise Server (and the
stand-in the end-to-end run uses) work the same as github.com. Errors come out
as GitHubError carrying GitHub's own message, because "Bad credentials" or
"Not Found" is what tells the owner which setting to look at.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from deploypro.domain.errors import GitHubError

TIMEOUT = 20.0

#: Enough for every repository an account is likely to hold; a listing that
#: runs past it is cut off rather than holding a page load for a minute.
MAX_PAGES = 10

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "DeployPro",
}


async def _request(
    method: str,
    url: str,
    *,
    auth: str | None = None,
    json: dict | None = None,
    params: dict | None = None,
    allow_404: bool = False,
) -> httpx.Response | None:
    headers = dict(HEADERS)
    if auth:
        headers["Authorization"] = auth
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(
                method, url, headers=headers, json=json, params=params
            )
    except httpx.HTTPError as exc:
        raise GitHubError(f"Could not reach GitHub: {exc}") from exc
    if allow_404 and response.status_code == 404:
        return None
    if response.status_code >= 400:
        try:
            detail = response.json().get("message") or response.text
        except ValueError:
            detail = response.text
        raise GitHubError(f"GitHub said {response.status_code}: {detail[:300]}")
    return response


def _bearer(token: str) -> str:
    return f"Bearer {token}"


async def convert_manifest(api_url: str, code: str) -> dict:
    """Exchange the one-time code GitHub hands back after creating the app
    for the app itself: its id, private key and webhook secret. The code
    works once, within an hour."""
    response = await _request("POST", f"{api_url}/app-manifests/{code}/conversions")
    assert response is not None
    return response.json()


async def get_installation(api_url: str, jwt: str, installation_id: int) -> dict:
    """An installation of this app. Asking with the app's own JWT is also the
    proof that the installation is this app's and not somebody else's."""
    response = await _request(
        "GET", f"{api_url}/app/installations/{installation_id}", auth=_bearer(jwt)
    )
    assert response is not None
    return response.json()


async def repo_installation(api_url: str, jwt: str, full_name: str) -> dict | None:
    """The installation that can read `owner/name`, or None if none can."""
    response = await _request(
        "GET",
        f"{api_url}/repos/{full_name}/installation",
        auth=_bearer(jwt),
        allow_404=True,
    )
    return response.json() if response is not None else None


async def create_token(
    api_url: str,
    jwt: str,
    installation_id: int,
    *,
    repositories: list[str] | None = None,
) -> tuple[str, datetime]:
    """An installation token, valid for an hour.

    With `repositories`, the token reads only those (names without the
    owner) and only their contents: the token handed to git for a clone can
    read the one repository being built and nothing else.
    """
    body: dict = {}
    if repositories:
        body = {"repositories": repositories, "permissions": {"contents": "read"}}
    response = await _request(
        "POST",
        f"{api_url}/app/installations/{installation_id}/access_tokens",
        auth=_bearer(jwt),
        json=body or None,
    )
    assert response is not None
    data = response.json()
    expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    return data["token"], expires


async def list_repos(api_url: str, token: str) -> list[dict]:
    """Every repository an installation token can see, most recent first."""
    repos: list[dict] = []
    url: str | None = f"{api_url}/installation/repositories"
    params: dict | None = {"per_page": 100}
    for _ in range(MAX_PAGES):
        if url is None:
            break
        response = await _request("GET", url, auth=_bearer(token), params=params)
        assert response is not None
        repos.extend(response.json().get("repositories", []))
        url = response.links.get("next", {}).get("url")
        params = None  # the next link carries its own query
    return repos


async def get_repo(api_url: str, token: str, full_name: str) -> dict:
    response = await _request("GET", f"{api_url}/repos/{full_name}", auth=_bearer(token))
    assert response is not None
    return response.json()
