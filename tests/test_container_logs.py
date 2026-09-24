"""Reading a container's output.

This is the one thing a failed deployment is judged by, so it gets a test that
runs a real subprocess rather than a mock: the bug it guards against was
invisible to any test that stubbed the process boundary, because the boundary
was where the mistake lived.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from deploypro.adapters import containers


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """Put a scripted `docker` first on PATH.

    The real daemon is not needed and would not help: what is under test is
    which of the CLI's streams the adapter reads.
    """

    def install(script: str) -> Path:
        binary = tmp_path / "docker"
        binary.write_text("#!/bin/sh\n" + script)
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        return binary

    return install


async def test_a_traceback_on_stderr_is_captured(fake_docker):
    """The regression. `docker logs` reproduces the container's stderr on its
    own stderr, so an adapter that reads only stdout drops every traceback —
    and a crashing process says why it crashed on stderr.

    This shipped: a deploy failed, the log printed "last 100 lines from the
    container" followed by nothing, and the container was destroyed straight
    afterwards, so the explanation existed nowhere at all.
    """
    fake_docker(
        'echo "Traceback (most recent call last):" >&2\n'
        'echo "ConfigError: JWT_SECRET is required but not set" >&2\n'
    )
    output = await containers.logs("any-container")
    assert "ConfigError: JWT_SECRET is required but not set" in output


async def test_stdout_is_captured_too(fake_docker):
    fake_docker('echo "INFO: Application startup complete."\n')
    assert "Application startup complete" in await containers.logs("any-container")


async def test_both_streams_arrive_in_order(fake_docker):
    """Merged at the pipe rather than concatenated afterwards, so an app that
    interleaves a log line and a traceback is still readable."""
    fake_docker('echo "INFO: starting"\necho "ERROR: boom" >&2\necho "INFO: exiting"\n')
    output = await containers.logs("any-container")
    assert output.index("starting") < output.index("boom") < output.index("exiting")


async def test_a_failure_to_read_explains_itself(fake_docker):
    """Never raise from here: this runs while a deployment is already failing,
    and an exception would replace the diagnosis with a different one."""
    fake_docker('echo "No such container: nope" >&2\nexit 1\n')
    output = await containers.logs("nope")
    assert "could not read" in output


async def test_no_output_is_not_silently_empty(fake_docker):
    """An empty string is a legitimate answer, and the caller is responsible
    for saying so rather than printing a heading over nothing."""
    fake_docker("exit 0\n")
    assert await containers.logs("quiet") == ""
