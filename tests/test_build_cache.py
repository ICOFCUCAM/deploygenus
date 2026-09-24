"""Bounding BuildKit's cache.

Driven through a scripted `docker` on PATH rather than a mock, because what is
under test is the command line handed to a tool whose flags differ by version.
A mock would assert my own assumptions back at me.
"""

from __future__ import annotations

import os
import stat

import pytest

from deploypro.adapters import containers
from deploypro.adapters.containers import DockerError


@pytest.fixture
def docker_recording(tmp_path, monkeypatch):
    """Install a fake `docker` that records its arguments and can fail on cue."""
    calls = tmp_path / "calls.txt"

    def install(script: str = 'echo "Total reclaimed space: 1.2GB"') -> list[str]:
        binary = tmp_path / "docker"
        binary.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n{script}\n')
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        return []

    def recorded() -> list[str]:
        return calls.read_text().splitlines() if calls.exists() else []

    install.recorded = recorded
    return install


async def test_age_alone_when_no_ceiling_is_set(docker_recording):
    docker_recording()
    await containers.prune_build_cache(older_than_hours=168)
    lines = docker_recording.recorded()
    assert len(lines) == 1
    assert "until=168h" in lines[0]


async def test_a_ceiling_runs_a_second_prune(docker_recording):
    """The point of the change. The age filter cannot touch a cache that grew
    twelve gigabytes yesterday, because none of it is old."""
    docker_recording()
    await containers.prune_build_cache(older_than_hours=168, max_bytes=8 * 1024**3)
    lines = docker_recording.recorded()
    assert len(lines) == 2
    assert "until=168h" in lines[0]
    assert "--keep-storage=8589934592" in lines[1]


async def test_it_falls_back_when_keep_storage_was_renamed(docker_recording):
    """Docker renamed the flag to --max-used-space on newer buildx. A platform
    that installs itself on whatever host it is given cannot assume either."""
    docker_recording(
        'case "$*" in\n'
        '  *keep-storage*) echo "unknown flag: --keep-storage" >&2; exit 125 ;;\n'
        '  *) echo "Total reclaimed space: 3.4GB" ;;\n'
        "esac"
    )
    result = await containers.prune_build_cache(
        older_than_hours=168, max_bytes=4 * 1024**3
    )
    lines = docker_recording.recorded()
    assert any("--keep-storage" in line for line in lines)
    assert any("--max-used-space=4294967296" in line for line in lines)
    assert "3.4GB" in result


async def test_a_real_failure_is_not_mistaken_for_a_missing_flag(docker_recording):
    """Only an unknown flag moves on to the next spelling. A daemon that is
    down must not be reported as a successful prune."""
    docker_recording('echo "Cannot connect to the Docker daemon" >&2\nexit 1')
    with pytest.raises(DockerError):
        await containers.prune_build_cache(older_than_hours=168, max_bytes=8 * 1024**3)


async def test_neither_flag_supported_still_prunes_by_age(docker_recording):
    """An ancient Docker should lose the ceiling, not the whole sweep."""
    docker_recording(
        'case "$*" in\n'
        '  *storage*|*used-space*) echo "unknown flag" >&2; exit 125 ;;\n'
        '  *) echo "Total reclaimed space: 900MB" ;;\n'
        "esac"
    )
    result = await containers.prune_build_cache(
        older_than_hours=168, max_bytes=8 * 1024**3
    )
    assert "900MB" in result
