"""What DeployPro asks the Docker daemon to do, without a daemon.

`_capture` is the single point every command passes through, so replacing it
records the exact argument lists — which is what matters: a mount that is
missing from `docker run` is data lost on the next deploy, and a rename sent
to the wrong name is a worker killed mid-render.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deploypro.adapters import containers
from deploypro.domain.storage import Mount


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.responses: dict[str, str] = {}
        self.failures: dict[str, str] = {}

    async def capture(self, args, *, timeout=120):
        self.calls.append(list(args))
        for prefix, message in self.failures.items():
            if " ".join(args).startswith(prefix):
                raise containers.DockerError(message)
        for prefix, out in self.responses.items():
            if " ".join(args).startswith(prefix):
                return out
        return "0123456789abcdef\n"

    def called(self, verb: str) -> list[list[str]]:
        return [call for call in self.calls if call[0] == verb]


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(containers, "_capture", fake.capture)
    return fake


MOUNTS = (Mount(volume="deploypro_bv_recordings", path="/data"),)


class TestRun:
    def spec(self, **kwargs) -> containers.RunSpec:
        base = dict(
            image="deploypro/bv:abc",
            name="deploypro-bv-1234abcd",
            network="deploypro",
            host="bv-1234abcd.deploys.example.com",
            port=8080,
            router="bv-1234abcd",
            memory_mb=512,
            cpu_shares=1.0,
            cert_resolver="",
        )
        base.update(kwargs)
        return containers.RunSpec(**base)

    async def test_mounts_volumes_by_name_never_as_host_paths(self, docker):
        await containers.run(self.spec(volumes=MOUNTS))
        args = docker.called("run")[0]
        i = args.index("--mount")
        assert args[i + 1] == "type=volume,src=deploypro_bv_recordings,dst=/data"
        assert "-v" not in args

    async def test_carries_the_stop_timeout_on_the_container_itself(self, docker):
        await containers.run(self.spec(stop_timeout=1800))
        assert "--stop-timeout=1800" in docker.called("run")[0]

    async def test_a_project_without_volumes_mounts_nothing(self, docker):
        await containers.run(self.spec())
        assert "--mount" not in docker.called("run")[0]


class TestProcesses:
    def spec(self, **kwargs) -> containers.TaskSpec:
        base = dict(
            image="deploypro/bv:abc",
            name="deploypro-bv-render-0",
            network="deploypro",
            command="node worker.js",
            memory_mb=2048,
        )
        base.update(kwargs)
        return containers.TaskSpec(**base)

    async def test_a_worker_gets_the_volumes_and_the_stop_timeout(self, docker):
        await containers.run_worker(self.spec(volumes=MOUNTS, stop_timeout=1800))
        args = docker.called("run")[0]
        assert "type=volume,src=deploypro_bv_recordings,dst=/data" in args
        assert "--stop-timeout=1800" in args
        # The image and command still come last, after every option.
        assert args[-3:] == ["deploypro/bv:abc", "node", "worker.js"]

    def test_a_job_gets_the_volumes_too(self):
        args = containers._process_args(self.spec(volumes=MOUNTS))
        assert "type=volume,src=deploypro_bv_recordings,dst=/data" in args

    async def test_stop_outlasts_its_own_grace_period(self, monkeypatch):
        seen = {}

        async def capture(args, *, timeout=120):
            seen["timeout"] = timeout
            return ""

        monkeypatch.setattr(containers, "_capture", capture)
        await containers.stop("abc", timeout=1800)
        assert seen["timeout"] > 1800


def inspect_output(*, name="/deploypro-bv-render-0", running=True, signal=None):
    # As Docker prints it: StopSignal is absent, not empty, when the image
    # does not set one.
    config = {"Image": "deploypro/bv:abc"}
    if signal:
        config["StopSignal"] = signal
    return json.dumps(
        {"id": "c0ffee" * 10, "name": name, "running": running, "config": config}
    )


class TestDrain:
    async def test_renames_then_signals_by_id_without_waiting(self, docker):
        docker.responses["inspect"] = inspect_output()
        await containers.drain("deploypro-bv-render-0", grace=1800, now=1_000_000)

        verbs = [call[0] for call in docker.calls]
        assert verbs == ["inspect", "rename", "update", "kill"]
        rename, update, kill = docker.calls[1:]
        # The old name is freed for the successor; the deadline is in the new one.
        assert rename == [
            "rename",
            "c0ffee" * 10,
            "deploypro-bv-render-0.draining.1001800",
        ]
        # Everything after the rename goes by id: the old name is about to be
        # someone else's.
        assert update == ["update", "--restart=no", "c0ffee" * 10]
        assert kill == ["kill", "--signal", "SIGTERM", "c0ffee" * 10]
        assert not docker.called("stop")

    async def test_honours_the_images_own_stop_signal(self, docker):
        docker.responses["inspect"] = inspect_output(signal="SIGQUIT")
        await containers.drain("deploypro-bv-render-0", grace=30, now=0)
        assert docker.called("kill")[0][2] == "SIGQUIT"

    async def test_a_container_that_already_exited_is_just_removed(self, docker):
        docker.responses["inspect"] = inspect_output(running=False)
        await containers.drain("deploypro-bv-render-0", grace=30, now=0)
        assert [call[0] for call in docker.calls] == ["inspect", "rm"]

    async def test_a_container_that_is_already_gone_is_not_an_error(self, docker):
        docker.failures["inspect"] = "Error: No such object: deploypro-bv-render-0"
        await containers.drain("deploypro-bv-render-0", grace=30, now=0)
        assert [call[0] for call in docker.calls] == ["inspect"]

    async def test_draining_twice_keeps_the_first_deadline(self, docker):
        docker.responses["inspect"] = inspect_output(
            name="/deploypro-bv-render-0.draining.500"
        )
        await containers.drain("x", grace=1800, now=1_000_000)
        assert not docker.called("rename")


class TestSweep:
    def listing(self, *containers_):
        return "\n".join(json.dumps(c) for c in containers_)

    async def test_removes_the_finished_and_the_overdue_only(self, docker):
        docker.responses["ps"] = self.listing(
            {"Names": "deploypro-bv-render-0.draining.100", "State": "exited"},
            {"Names": "deploypro-bv-render-1.draining.100", "State": "running"},
            {"Names": "deploypro-bv-render-2.draining.999", "State": "running"},
            {"Names": "deploypro-bv-render-3", "State": "exited"},
        )
        finished, killed = await containers.sweep_draining(now=500)
        assert (finished, killed) == (1, 1)
        removed = [call[-1] for call in docker.called("rm")]
        assert removed == [
            "deploypro-bv-render-0.draining.100",
            "deploypro-bv-render-1.draining.100",
        ]

    async def test_a_worker_listing_ignores_draining_containers(self, docker):
        docker.responses["ps"] = (
            "deploypro-bv-render-0\ndeploypro-bv-render-0.draining.99\n"
        )
        assert await containers.list_process_containers("bv") == ["deploypro-bv-render-0"]


class TestVolumes:
    async def test_an_existing_volume_is_left_alone(self, docker):
        assert not await containers.ensure_volume("deploypro_bv_rec", labels={})
        assert not docker.called("volume")[1:]

    async def test_a_missing_volume_is_created_with_deploypros_labels(self, docker):
        docker.failures["volume inspect"] = "Error: No such volume"
        created = await containers.ensure_volume(
            "deploypro_bv_rec", labels={"deploypro.owner": "deploypro"}
        )
        assert created
        assert docker.calls[-1] == [
            "volume",
            "create",
            "--label",
            "deploypro.owner=deploypro",
            "deploypro_bv_rec",
        ]


async def test_a_preview_never_mounts_the_projects_volumes(monkeypatch):
    from deploypro.domain.models import EnvTarget
    from deploypro.engine import storage
    from tests import fakes

    async def boom(_project_id):
        raise AssertionError("a preview must not even look up the volumes")

    monkeypatch.setattr(storage.volume_repo, "list_for_project", boom)
    assert await storage.mounts_for(fakes.project(), target=EnvTarget.PREVIEW) == ()


class TestImages:
    async def test_the_sweep_lists_only_this_installations_images(self, docker):
        docker.responses["images"] = "deploypro/bv:abc\n"
        assert await containers.list_deploypro_images("deploypro") == ["deploypro/bv:abc"]
        args = docker.called("images")[0]
        assert "label=deploypro.instance=deploypro" in args

    async def test_a_build_is_labelled_with_its_installation(self, monkeypatch, tmp_path):
        seen = {}

        async def stream(args, *, log, timeout, buildkit):
            seen["args"] = args

        monkeypatch.setattr(containers, "_stream", stream)

        async def log(_line):
            pass

        await containers.build(
            context=tmp_path,
            dockerfile=tmp_path / "Dockerfile",
            tag="deploypro/bv:abc",
            secret_env_file=None,
            log=log,
            timeout=60,
            labels={"deploypro.instance": "deploypro"},
        )
        args = seen["args"]
        assert args[args.index("--label") + 1] == "deploypro.instance=deploypro"
        assert args[-1] == str(tmp_path)


@pytest.mark.parametrize(
    "message",
    [
        "Error response from daemon: No such container: abc",
        "error: no such object: 5b9451403a7f",
        "Error: No such volume: deploypro_bv_recordings",
    ],
)
def test_every_spelling_of_already_gone_is_recognised(message):
    assert containers.is_missing(containers.DockerError(message))


async def test_draining_a_container_deleted_by_hand_is_not_an_error(docker):
    """What the end-to-end run's disaster drill hit on Docker 29."""
    docker.failures["inspect"] = "error: no such object: 5b9451403a7f"
    await containers.drain("5b9451403a7f", grace=10, now=0)


async def test_a_deployment_router_inherits_the_wildcard_certificate(docker):
    """No tls label at all. Traefik applies the entrypoint's TLS default (the
    wildcard resolver) only when a router's TLS is unset, and `tls=true` sets
    it to an empty value — which is how the first real install ended up
    serving Traefik's self-signed certificate with no ACME request at all."""
    await containers.run(TestRun().spec(cert_resolver="le"))
    args = docker.called("run")[0]
    labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
    assert "traefik.http.routers.bv-1234abcd.entrypoints=websecure" in labels
    assert not [label for label in labels if ".tls" in label]


def test_the_dashboard_router_inherits_it_too():
    compose = (Path(__file__).parent.parent / "docker-compose.yml").read_text()
    assert "routers.deploypro-api.entrypoints: websecure" in compose
    assert "routers.deploypro-api.tls" not in compose
