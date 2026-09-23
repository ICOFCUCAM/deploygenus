"""Deploy keys: made properly, used only for one command, never left behind."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization

from deploypro.adapters import crypto, sshkeys
from deploypro.domain.repo_url import GITHUB_KNOWN_HOSTS
from deploypro.engine import gitaccess
from tests import fakes


def test_a_generated_key_is_a_real_ed25519_pair():
    private, public = sshkeys.generate("deploypro@blog")
    loaded = serialization.load_ssh_private_key(private.encode(), password=None)
    derived = (
        loaded.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )
    assert public == f"{derived} deploypro@blog"
    assert public.startswith("ssh-ed25519 ")


def test_every_key_is_different():
    assert sshkeys.generate("a")[1] != sshkeys.generate("a")[1]


@dataclass
class FakeSettings:
    build_root: Path
    master_key: str


@pytest.fixture
def settings(tmp_path):
    return FakeSettings(build_root=tmp_path, master_key=Fernet.generate_key().decode())


class TestKnownHosts:
    def test_github_is_pinned_from_the_start(self, settings):
        path = gitaccess.known_hosts_file(settings)
        assert all(line in path.read_text() for line in GITHUB_KNOWN_HOSTS)
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_hosts_learned_later_are_kept_and_github_is_not_duplicated(self, settings):
        path = gitaccess.known_hosts_file(settings)
        path.write_text(path.read_text() + "[127.0.0.1]:2222 ssh-ed25519 AAAAlearned\n")
        path = gitaccess.known_hosts_file(settings)
        text = path.read_text()
        assert "AAAAlearned" in text
        assert text.count("github.com ssh-ed25519") == 1


class TestSshCommand:
    def test_uses_only_the_given_key_and_never_prompts(self, tmp_path):
        command = gitaccess.ssh_command(
            known_hosts=tmp_path / "known_hosts", identity=tmp_path / "id_ed25519"
        )
        assert "IdentitiesOnly=yes" in command
        assert "BatchMode=yes" in command
        assert "StrictHostKeyChecking=accept-new" in command
        assert f"UserKnownHostsFile={tmp_path}/known_hosts" in command

    def test_a_path_with_spaces_stays_one_argument(self, tmp_path):
        import shlex

        weird = tmp_path / "a dir" / "id"
        argv = shlex.split(gitaccess.ssh_command(known_hosts=weird, identity=weird))
        assert str(weird) in argv


class TestGitEnv:
    @pytest.fixture
    def stored(self, monkeypatch, settings):
        private, _ = sshkeys.generate("x")
        box = {"encrypted": crypto.encrypt(private, key=settings.master_key)}

        async def get(_id):
            return box["encrypted"]

        monkeypatch.setattr(gitaccess.project_repo, "get_deploy_key_encrypted", get)
        return private

    async def test_https_repositories_get_nothing(self, settings, stored):
        project = fakes.project(repo_url="https://github.com/o/r.git")
        async with gitaccess.git_env(project, settings) as env:
            assert env == {}

    async def test_the_key_exists_only_for_the_command_and_only_to_its_owner(
        self, settings, stored
    ):
        project = fakes.project(repo_url="git@github.com:o/r.git")
        async with gitaccess.git_env(project, settings) as env:
            command = env["GIT_SSH_COMMAND"]
            key = Path(command.split(" -i ")[1].split()[0])
            assert key.read_text() == stored
            assert stat.S_IMODE(key.stat().st_mode) == 0o600
            assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
            # Only a path in the environment, never the key itself.
            assert "PRIVATE KEY" not in command
        assert not key.exists()
        assert not key.parent.exists()

    async def test_the_key_is_removed_even_when_git_fails(self, settings, stored):
        project = fakes.project(repo_url="git@github.com:o/r.git")
        with pytest.raises(RuntimeError):
            async with gitaccess.git_env(project, settings) as env:
                key = Path(env["GIT_SSH_COMMAND"].split(" -i ")[1].split()[0])
                raise RuntimeError("git clone failed")
        assert not key.exists()

    async def test_without_a_key_ssh_still_checks_host_keys(self, settings, monkeypatch):
        async def none(_id):
            return None

        monkeypatch.setattr(gitaccess.project_repo, "get_deploy_key_encrypted", none)
        project = fakes.project(repo_url="git@github.com:o/r.git")
        async with gitaccess.git_env(project, settings) as env:
            assert "UserKnownHostsFile=" in env["GIT_SSH_COMMAND"]
            assert " -i " not in env["GIT_SSH_COMMAND"]
        assert os.listdir(settings.build_root / "ssh") == ["known_hosts"]
