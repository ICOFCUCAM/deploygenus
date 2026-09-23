"""The GitHub App, as pure functions: what DeployPro asks GitHub to create,
how it proves it is that app, and how a repository is named.

No I/O here. The HTTP calls are in deploypro.adapters.github and the database
in deploypro.repositories.github; what is below is the part worth testing
hardest, because a mistake in it is either a leaked token or an app that
quietly never deploys.
"""

from __future__ import annotations

import base64
import json
import re
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

#: What the app may do, and no more. `contents: read` clones a repository
#: and `metadata: read` lists them; nothing can be written back. GitHub shows
#: this list to the owner before the app is created and again on install.
PERMISSIONS = {"contents": "read", "metadata": "read"}

#: The events the app is sent. Installation events (added, removed) always
#: arrive without being asked for.
EVENTS = ["push"]

#: GitHub refuses app names longer than this.
MAX_APP_NAME = 34

#: An app token lives an hour; one this close to expiry is replaced before
#: use rather than risk it expiring halfway through a clone.
TOKEN_MARGIN_SECONDS = 300

_FULL_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")


def app_name(deploy_domain: str) -> str:
    """A name for the app. GitHub app names are unique across all of GitHub,
    so it carries the domain; the owner can change it on the next page."""
    return f"DeployPro {deploy_domain}"[:MAX_APP_NAME].strip()


def manifest(*, dashboard_url: str, deploy_domain: str) -> dict:
    """The app GitHub is asked to create, in its manifest format.

    Private (`public: false`): only the account that made it can install it.
    Anyone else installing it would get their repositories deployed on this
    server, which is not what a one-owner platform is for.
    """
    return {
        "name": app_name(deploy_domain),
        "url": dashboard_url,
        "hook_attributes": {"url": f"{dashboard_url}/github/webhook", "active": True},
        "redirect_url": f"{dashboard_url}/github/created",
        "setup_url": f"{dashboard_url}/github/installed",
        "setup_on_update": True,
        "public": False,
        "default_permissions": dict(PERMISSIONS),
        "default_events": list(EVENTS),
    }


def is_full_name(value: str) -> bool:
    return bool(_FULL_NAME.match(value or ""))


def repo_from_url(url: str, *, github_url: str = "https://github.com") -> str | None:
    """`owner/name` for a repository URL on this GitHub, or None.

    Accepts the https form and both SSH forms, with or without `.git`, so a
    project created from any URL GitHub offers can be linked to the app.
    """
    host = urlsplit(github_url).hostname or ""
    url = (url or "").strip()
    path = None
    ssh = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+)$", url)
    if ssh:
        if ssh.group(1).lower() == host.lower():
            path = ssh.group(2)
    else:
        parts = urlsplit(url)
        if parts.scheme == "https" and (parts.hostname or "").lower() == host.lower():
            path = parts.path.lstrip("/")
    if not path:
        return None
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path if is_full_name(path) else None


def clone_url(full_name: str, *, github_url: str = "https://github.com") -> str:
    return f"{github_url}/{full_name}.git"


def git_auth_env(repo_url: str, token: str) -> dict[str, str]:
    """Environment that makes git send `token` to this repository's host.

    Passed as configuration through the environment (GIT_CONFIG_COUNT and
    friends, git 2.31+), so the token is never in the URL, never on a command
    line where `ps` shows it, never in .git/config of the checkout, and never
    in git's own error messages, which print the URL. The header is scoped to
    the repository's origin, so a redirect elsewhere does not carry it.
    """
    parts = urlsplit(repo_url)
    origin = f"{parts.scheme}://{parts.netloc}/"
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{origin}.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


# ---------------------------------------------------------------------------
# Proving to GitHub that a request comes from the app
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: int | str, private_key_pem: str, *, now: float | None = None) -> str:
    """A ten-minute JWT signed with the app's private key (RS256).

    Issued a minute in the past, as GitHub recommends, so a server clock that
    runs slightly ahead of GitHub's is not refused as "issued in the future".
    """
    issued = int(now if now is not None else time.time()) - 60
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {"iat": issued, "exp": issued + 600, "iss": str(app_id)}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("A GitHub App's private key is an RSA key")
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(signature)
