"""The Docker daemon, driven through its CLI.

The CLI rather than the SDK, for one reason that outweighs the tidiness of a
library: BuildKit. Cache mounts, secret mounts and `--progress=plain` are how
builds stay fast and how build-time secrets stay out of image layers, and the
Python SDK's build support predates all of it.

Everything here is a thin, single-purpose coroutine over a subprocess, which
also makes the whole module replaceable by a fake in tests — there is no
hidden state and no connection to hold.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from deploypro.domain.storage import Mount, draining_deadline, draining_name

LogSink = Callable[[str], Awaitable[None]]

#: Label carried by everything DeployPro creates, so reconciliation can find its
#: own containers without mistaking a hand-started one for an orphan.
OWNER_LABEL = "deploypro.owner"
OWNER_VALUE = "deploypro"
DEPLOYMENT_LABEL = "deploypro.deployment"
PROJECT_LABEL = "deploypro.project"
#: Which DeployPro installation built an image — its network name, which is
#: already unique per installation on a host. The image sweep deletes only its
#: own installation's images, so a second installation on the same daemon (a
#: staging copy, the end-to-end run) can never sweep away the first one's
#: rollback targets as "images of a project that no longer exists".
INSTANCE_LABEL = "deploypro.instance"


class DockerError(RuntimeError):
    """A docker command exited non-zero. The output is in the message."""


def is_missing(exc: Exception) -> bool:
    """Whether Docker's error means "no such container / object / volume".

    Case-insensitively, because Docker is not consistent about it: 29.x says
    "Error response from daemon: No such container" from `rm` and
    "error: no such object" from `inspect`. Matching one spelling made every
    "already gone, carry on" path raise instead — found when the end-to-end
    run deleted a project's containers by hand and the next deploy failed.
    """
    return "no such" in str(exc).lower()


@dataclass(frozen=True, slots=True)
class RunSpec:
    image: str
    name: str
    network: str
    #: The deployment's permanent hostname. Never changes, so it can live in a
    #: container label; production domains cannot, and go through the router's
    #: file provider instead.
    host: str
    port: int
    router: str
    memory_mb: int
    cpu_shares: float
    cert_resolver: str
    env_file: Path | None = None
    #: Values that cannot be expressed in an env file — anything containing a
    #: newline. Passed as arguments instead. See deploypro.engine.environment.
    inline_env: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    volumes: tuple[Mount, ...] = ()
    #: Seconds between the stop signal and SIGKILL. Set on the container
    #: itself, so it holds for stops DeployPro never issues — a daemon restart, a
    #: host shutdown, an operator's `docker stop`.
    stop_timeout: int = 10


async def build(
    *,
    context: Path,
    dockerfile: Path,
    tag: str,
    secret_env_file: Path | None,
    log: LogSink,
    timeout: int,
    labels: dict[str, str] | None = None,
) -> None:
    """Build an image, streaming every line to `log` as it happens.

    The environment file is passed as a BuildKit secret, not a build argument.
    A build argument is recorded in the image's metadata and `docker history`
    prints it; a secret mount exists only for the RUN instruction that asks
    for it and is in no layer afterwards.
    """
    args = [
        "build",
        "--progress=plain",
        "--file",
        str(dockerfile),
        "--tag",
        tag,
        # Without this, a rebuild after a base image update silently keeps
        # serving the old base. Deploys are infrequent enough that the cost of
        # checking is irrelevant next to shipping a stale CVE.
        "--pull",
    ]
    if secret_env_file is not None:
        args += ["--secret", f"id=env,src={secret_env_file}"]
    for key, value in (labels or {}).items():
        args += ["--label", f"{key}={value}"]
    args.append(str(context))

    await _stream(args, log=log, timeout=timeout, buildkit=True)


async def run(spec: RunSpec, *, log: LogSink | None = None) -> str:
    """Start a container and return its id.

    The container publishes no port. It is reachable only from the shared
    network, by the router, by container name — so a deployment cannot be
    reached except through the router, and two deployments cannot collide on a
    host port because neither has one.
    """
    labels = {
        OWNER_LABEL: OWNER_VALUE,
        "traefik.enable": "true",
        "traefik.docker.network": spec.network,
        f"traefik.http.routers.{spec.router}.rule": f"Host(`{spec.host}`)",
        f"traefik.http.services.{spec.router}.loadbalancer.server.port": str(spec.port),
        **spec.labels,
    }
    if spec.cert_resolver:
        labels[f"traefik.http.routers.{spec.router}.entrypoints"] = "websecure"
        # No TLS label at all — not even `tls=true`. The router inherits the
        # websecure entrypoint's default TLS: the `wildcard` resolver and the
        # *.deploy-domain certificate. Traefik applies that default only to a
        # router whose TLS is unset (applyModel: `if cp.TLS == nil`), and
        # `tls=true` sets it to an empty value, which silently opts the router
        # out: no resolver, no certificate request, Traefik's self-signed
        # default served instead. That is what the first real install did.
        #
        # And no resolver of its own either: naming one would ask the CA for a
        # certificate per deployment hostname. Let's Encrypt issues 50 new
        # certificates per registered domain per week, and this platform
        # mints a brand new hostname on every deploy — about seven deploys a
        # day would exhaust the week and then fail to issue anything, including
        # for the custom domains that carry the real traffic. One wildcard,
        # issued once, has no such ceiling.
    else:
        labels[f"traefik.http.routers.{spec.router}.entrypoints"] = "web"

    args = [
        "run",
        "--detach",
        "--name",
        spec.name,
        "--network",
        spec.network,
        # Survives a host reboot without the control plane having to notice.
        "--restart",
        "unless-stopped",
        f"--memory={spec.memory_mb}m",
        f"--cpus={spec.cpu_shares}",
        # A fork bomb in one site should cost that site and nothing else.
        "--pids-limit=512",
        # Nothing built here needs a capability, and a process that cannot
        # acquire new privileges cannot escalate through a setuid binary it
        # happens to find in its own image.
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        # Container logs are the run stream. Capped so a chatty app cannot
        # fill the host disk and take every other site down with it.
        "--log-opt",
        "max-size=10m",
        "--log-opt",
        "max-file=3",
        f"--env=PORT={spec.port}",
        f"--stop-timeout={spec.stop_timeout}",
        *_mount_args(spec.volumes),
    ]
    for key, value in labels.items():
        args += ["--label", f"{key}={value}"]
    if spec.env_file is not None:
        args += ["--env-file", str(spec.env_file)]
    for key, value in spec.inline_env.items():
        args += ["--env", f"{key}={value}"]
    args.append(spec.image)

    out = await _capture(args)
    container_id = out.strip().splitlines()[-1]
    if log:
        await log(f"started container {container_id[:12]}")
    return container_id


async def stop(container_id: str, *, timeout: int = 10) -> None:
    """Stop a container, tolerating one that is already gone.

    Blocks for up to `timeout`. Anything replacing a container during a deploy
    uses `drain` instead, which does not.
    """
    try:
        # The subprocess bound has to outlast the grace period, or a container
        # that takes its full allowance reads as a hung Docker daemon.
        await _capture(
            ["stop", "--time", str(timeout), container_id], timeout=timeout + 60
        )
    except DockerError as exc:
        if not is_missing(exc):
            raise


async def remove(container_id: str, *, force: bool = True) -> None:
    args = ["rm"] + (["--force"] if force else []) + [container_id]
    try:
        await _capture(args)
    except DockerError as exc:
        if not is_missing(exc):
            raise


async def remove_by_name(name: str) -> None:
    """Clear a name before reusing it.

    A previous deploy that died between `docker run` and the database write
    leaves a container holding the name; without this the retry fails on a
    name conflict and looks like a build problem.
    """
    await remove(name, force=True)


async def state(name_or_id: str) -> str | None:
    """Docker's status word — running, restarting, exited… — or None if there
    is no such container."""
    try:
        out = await _capture(["inspect", "--format", "{{.State.Status}}", name_or_id])
    except DockerError:
        return None
    return out.strip() or None


async def restart_count(container_id: str) -> int:
    """How many times the restart policy has restarted this container."""
    try:
        out = await _capture(["inspect", "--format", "{{.RestartCount}}", container_id])
    except DockerError:
        return 0
    try:
        return int(out.strip())
    except ValueError:
        return 0


async def is_running(container_id: str) -> bool:
    try:
        out = await _capture(["inspect", "--format", "{{.State.Running}}", container_id])
    except DockerError:
        return False
    return out.strip() == "true"


async def container_ip(container_id: str, network: str) -> str | None:
    """The container's address on the shared network.

    Used by the health check, which talks to the container directly rather
    than through the router — the question being asked is "is this app up",
    and going through the router would also be asking "is the router's
    configuration right", which is a different question with a different fix.
    """
    template = (
        "{{with index .NetworkSettings.Networks " + json.dumps(network) + "}}"
        "{{.IPAddress}}{{end}}"
    )
    try:
        out = await _capture(["inspect", "--format", template, container_id])
    except DockerError:
        return None
    return out.strip() or None


async def logs(container_id: str, *, tail: int = 200) -> str:
    """The container's output, BOTH streams, oldest line first.

    `docker logs` reproduces the container's stdout on its stdout and the
    container's stderr on its stderr. Capturing only stdout therefore drops
    every traceback, every `ConfigError`, and every line a crashing process
    wrote on its way out — which is the entire reason anyone reads this.

    That is not hypothetical: it shipped. A deployment failed its health
    check, the deploy log printed "last 100 lines from the container" and then
    nothing at all, and the container was destroyed immediately afterwards, so
    the one explanation of the failure existed nowhere. Five consecutive
    deploys of the same app failed that way, and the cause turned out to be a
    single undeclared dependency the traceback named on line one.

    Hence `_capture` is not used here: the streams are merged at the pipe, so
    ordering between them survives too.
    """
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "logs",
        "--tail",
        str(tail),
        container_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_env(buildkit=False),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        proc.kill()
        return "(timed out reading the container's logs)"
    if proc.returncode != 0:
        return f"(could not read the container's logs: exit {proc.returncode})"
    return stdout.decode(errors="replace")


def _mount_args(mounts: tuple[Mount, ...]) -> list[str]:
    """`--mount` rather than `-v`: it refuses a volume name it does not
    recognise as one instead of treating it as a host path, so a typo cannot
    quietly bind-mount a directory of the host into a container."""
    args: list[str] = []
    for mount in mounts:
        args += ["--mount", f"type=volume,src={mount.volume},dst={mount.path}"]
    return args


async def ensure_volume(name: str, *, labels: dict[str, str]) -> bool:
    """Create a named volume if it does not exist. True if it was created.

    Created explicitly, before the first container that mounts it, so it
    carries DeployPro's labels — a volume Docker creates implicitly on `run` has
    none, and `deploypro doctor` could not tell it from one somebody made by hand.
    """
    try:
        await _capture(["volume", "inspect", name])
        return False
    except DockerError:
        pass
    args = ["volume", "create"]
    for key, value in labels.items():
        args += ["--label", f"{key}={value}"]
    await _capture([*args, name])
    return True


async def list_owned_volumes() -> list[str]:
    """Every volume DeployPro created, including ones no project mounts any more."""
    out = await _capture(
        [
            "volume",
            "ls",
            "--filter",
            f"label={OWNER_LABEL}={OWNER_VALUE}",
            "--format",
            "{{.Name}}",
        ]
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


async def drain(name_or_id: str, *, grace: int, now: float | None = None) -> None:
    """Ask a container to stop, and return without waiting for it to.

    The container is renamed out of the way first, which frees its name for a
    successor that can start immediately, and its restart policy is cleared so
    Docker does not bring it back when it exits. It then gets the stop signal
    and `grace` seconds to finish what it is doing; `sweep_draining` removes it
    once it has exited, or kills it once the deadline in its new name passes.

    This is what makes a long stop timeout affordable. Waiting in line instead
    would hold a deploy — or an HTTP request for a rollback — for as long as
    the slowest worker takes to finish a render.
    """
    deadline = int((now if now is not None else time.time()) + grace)
    # `.Config` whole, not `.Config.StopSignal`: Docker omits StopSignal when
    # the image does not set one, and a template naming a missing key fails
    # outright rather than printing nothing.
    template = (
        '{"id": {{json .Id}}, "name": {{json .Name}}, '
        '"running": {{json .State.Running}}, "config": {{json .Config}}}'
    )
    try:
        info = json.loads(await _capture(["inspect", "--format", template, name_or_id]))
    except DockerError as exc:
        if is_missing(exc):
            return
        raise
    # By id from here on: the name is about to change, and the old one may
    # belong to the successor a moment later.
    container_id = info["id"]
    current = info["name"].lstrip("/")

    if not info["running"]:
        await remove(container_id, force=True)
        return

    if draining_deadline(current) is None:
        await _capture(
            ["rename", container_id, draining_name(current, deadline=deadline)]
        )
    await _capture(["update", "--restart=no", container_id])
    try:
        signal = (info.get("config") or {}).get("StopSignal") or "SIGTERM"
        await _capture(["kill", "--signal", signal, container_id])
    except DockerError as exc:
        # Exited between the inspect and the signal: nothing left to ask.
        if "is not running" not in str(exc) and not is_missing(exc):
            raise
        await remove(container_id, force=True)


async def sweep_draining(*, now: float | None = None) -> tuple[int, int]:
    """Remove draining containers that finished; kill ones out of time.

    Returns (finished, killed).
    """
    moment = now if now is not None else time.time()
    finished = killed = 0
    for container in await list_owned():
        name = container.get("Names", "")
        deadline = draining_deadline(name)
        if deadline is None:
            continue
        if container.get("State") != "running":
            await remove(name, force=True)
            finished += 1
        elif moment >= deadline:
            await remove(name, force=True)
            killed += 1
    return finished, killed


async def ensure_network(name: str) -> None:
    try:
        await _capture(["network", "inspect", name])
    except DockerError:
        await _capture(["network", "create", name])


async def list_owned() -> list[dict[str, str]]:
    """Every container DeployPro started, for reconciliation at boot.

    The database is the intent; Docker is the fact. They diverge whenever the
    host reboots, a container is killed by the OOM reaper, or someone runs
    `docker rm` by hand — so the worker compares them on startup rather than
    trusting the rows.
    """
    out = await _capture(
        [
            "ps",
            "--all",
            "--filter",
            f"label={OWNER_LABEL}={OWNER_VALUE}",
            "--format",
            "{{json .}}",
        ]
    )
    found = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            found.append(json.loads(line))
    return found


async def list_installation_containers(network: str) -> list[tuple[str, str]]:
    """(name, project slug) of every container this installation started.

    Scoped by the installation's network as well as the owner label: another
    DeployPro on the same daemon (a staging copy, the end-to-end run) labels
    its containers identically, and must never be mistaken for this one's.
    Stopped and draining containers are included; both still hold a name and,
    for a web deployment, a route.
    """
    out = await _capture(
        [
            "ps",
            "--all",
            "--filter",
            f"label={OWNER_LABEL}={OWNER_VALUE}",
            "--filter",
            f"network={network}",
            "--format",
            '{{.Names}}\t{{.Label "' + PROJECT_LABEL + '"}}',
        ]
    )
    found = []
    for line in out.splitlines():
        name, _, project = line.strip().partition("\t")
        if name:
            found.append((name, project.strip()))
    return found


async def image_exists(tag: str) -> bool:
    try:
        await _capture(["image", "inspect", tag])
    except DockerError:
        return False
    return True


async def list_deploypro_images(instance: str) -> list[str]:
    """This installation's `deploypro/<project>:<tag>` images, as `repo:tag`.

    Images without the instance label — built before it existed, or by hand —
    are not listed, and so are never swept: unknown means not ours to delete.
    """
    out = await _capture(
        [
            "images",
            "--filter",
            "reference=deploypro/*",
            "--filter",
            f"label={INSTANCE_LABEL}={instance}",
            "--format",
            "{{.Repository}}:{{.Tag}}",
        ]
    )
    return sorted({line.strip() for line in out.splitlines() if ":" in line.strip()})


async def prune_build_cache(*, older_than_hours: int) -> str:
    """Drop BuildKit cache nobody has used for a while.

    This is the layer cache and the package-manager caches together. Both
    regrow on the next build that needs them; neither is worth a full disk.
    """
    out = await _capture(
        [
            "builder",
            "prune",
            "--force",
            "--filter",
            f"until={older_than_hours}h",
        ],
        timeout=600,
    )
    lines = [line for line in out.splitlines() if line.strip()]
    return lines[-1] if lines else ""


async def export_volume(volume: str, image: str, dest: Path) -> None:
    """Write a volume's contents to `dest` as a tar stream (top directory
    `data/`).

    Through a container that is created and never started: `docker cp` reads
    a container's volumes whether or not it runs, so the image only has to
    exist — a `FROM scratch` image with no shell and no tar serves as well as
    any. Nothing about the app runs during a backup.
    """
    helper = await _capture(
        ["create", "--mount", f"type=volume,src={volume},dst=/data", image]
    )
    helper = helper.strip().splitlines()[-1]
    try:
        with dest.open("wb") as out:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "cp",
                f"{helper}:/data",
                "-",
                stdout=out,
                stderr=asyncio.subprocess.PIPE,
                env=_env(buildkit=False),
            )
            _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise DockerError(
                f"docker cp of volume {volume} failed: "
                f"{stderr.decode(errors='replace').strip()}"
            )
    finally:
        await remove(helper, force=True)


async def import_volume(volume: str, image: str, source: Path) -> None:
    """Replace nothing, add everything: unpack a tar written by
    `export_volume` into a volume. Existing files with the same names are
    overwritten; others are left alone."""
    helper = await _capture(
        ["create", "--mount", f"type=volume,src={volume},dst=/data", image]
    )
    helper = helper.strip().splitlines()[-1]
    try:
        with source.open("rb") as tar:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "cp",
                "-",
                f"{helper}:/",
                stdin=tar,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_env(buildkit=False),
            )
            _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise DockerError(
                f"docker cp into volume {volume} failed: "
                f"{stderr.decode(errors='replace').strip()}"
            )
    finally:
        await remove(helper, force=True)


async def prune_image(tag: str) -> bool:
    """Remove an image. False if Docker refused.

    An image still referenced by a container is not an error worth raising:
    it means something is still using it, which is correct.
    """
    try:
        await _capture(["image", "rm", tag])
    except DockerError:
        return False
    return True


# ---------------------------------------------------------------------------
# Process plumbing
# ---------------------------------------------------------------------------


def _env(buildkit: bool) -> dict[str, str]:
    env = dict(os.environ)
    if buildkit:
        env["DOCKER_BUILDKIT"] = "1"
    return env


async def _stream(args: list[str], *, log: LogSink, timeout: int, buildkit: bool) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_env(buildkit),
    )
    assert proc.stdout is not None

    async def pump() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                await log(line)

    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await log(f"timed out after {timeout}s — killed")
        raise DockerError(f"docker {args[0]} timed out after {timeout}s") from exc

    if proc.returncode != 0:
        raise DockerError(f"docker {args[0]} failed with exit code {proc.returncode}")


async def _capture(args: list[str], *, timeout: int = 120) -> str:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_env(buildkit=False),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        raise DockerError(f"docker {args[0]} timed out") from exc
    if proc.returncode != 0:
        raise DockerError(
            f"docker {shlex.join(args[:2])} failed ({proc.returncode}): "
            f"{stderr.decode(errors='replace').strip()}"
        )
    return stdout.decode(errors="replace")


# ---------------------------------------------------------------------------
# Processes: workers and one-shot jobs
# ---------------------------------------------------------------------------

PROCESS_LABEL = "deploypro.process"
ROLE_LABEL = "deploypro.role"

#: How much of a job's output to keep. Enough to diagnose a failure, bounded
#: so a job that prints a megabyte a second cannot fill the database. The tail
#: is kept rather than the head: the traceback is at the end.
MAX_OUTPUT_BYTES = 64_000


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """A container that is not a website.

    Shares the deployment's image, and deliberately carries no Traefik labels
    at all — a queue consumer with a public hostname is a mistake waiting to
    be found by a crawler.
    """

    image: str
    name: str
    network: str
    command: str | None
    memory_mb: int
    env_file: Path | None = None
    inline_env: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    volumes: tuple[Mount, ...] = ()
    stop_timeout: int = 10


@dataclass(frozen=True, slots=True)
class TaskResult:
    exit_code: int | None
    output: str
    timed_out: bool

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def command_argv(command: str | None) -> list[str]:
    """Turn a configured command into arguments for `docker run`.

    A command needing shell features gets a shell, explicitly. Everything else
    is split and passed directly, which matters more than it looks: a
    distroless image has no `/bin/sh`, so wrapping every command in one would
    make cron impossible on exactly the images this platform builds for Go.
    """
    if not command:
        return []
    if any(ch in command for ch in "|&;<>$*?`"):
        return ["/bin/sh", "-c", f"exec {command}"]
    return shlex.split(command)


def _process_args(spec: TaskSpec) -> list[str]:
    args = [
        "--name",
        spec.name,
        "--network",
        spec.network,
        f"--memory={spec.memory_mb}m",
        "--pids-limit=512",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--stop-timeout={spec.stop_timeout}",
        *_mount_args(spec.volumes),
    ]
    for key, value in spec.labels.items():
        args += ["--label", f"{key}={value}"]
    if spec.env_file is not None:
        args += ["--env-file", str(spec.env_file)]
    for key, value in spec.inline_env.items():
        args += ["--env", f"{key}={value}"]
    return args


async def run_worker(spec: TaskSpec) -> str:
    """Start a long-running process that serves no traffic."""
    await remove_by_name(spec.name)
    args = [
        "run",
        "--detach",
        "--restart",
        "unless-stopped",
        "--log-opt",
        "max-size=10m",
        "--log-opt",
        "max-file=3",
        # Explicit rather than merely absent: the Docker provider ignores
        # containers without this, but saying so leaves no doubt for anyone
        # reading `docker inspect` and wondering why the worker has no route.
        "--label",
        "traefik.enable=false",
        *_process_args(spec),
        spec.image,
        *command_argv(spec.command),
    ]
    out = await _capture(args)
    return out.strip().splitlines()[-1]


async def run_once(spec: TaskSpec, *, timeout: int) -> TaskResult:
    """Run a container to completion and return its exit code and output.

    Not `--rm`: the container is removed explicitly at the end, because a
    `--rm` container that is killed on timeout can disappear before its exit
    status is read, and "the job timed out" and "the job vanished" would
    become the same report.
    """
    await remove_by_name(spec.name)
    args = [
        "run",
        *_process_args(spec),
        "--label",
        "traefik.enable=false",
        spec.image,
        *command_argv(spec.command),
    ]

    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_env(buildkit=False),
    )
    assert proc.stdout is not None

    collected = bytearray()
    timed_out = False

    async def drain() -> None:
        assert proc.stdout is not None
        async for chunk in proc.stdout:
            collected.extend(chunk)
            # Trim as we go. Buffering the whole of a runaway job's output and
            # truncating at the end would mean holding it all in memory first,
            # which is the thing being defended against.
            if len(collected) > MAX_OUTPUT_BYTES * 2:
                del collected[: len(collected) - MAX_OUTPUT_BYTES]

    try:
        await asyncio.wait_for(asyncio.gather(drain(), proc.wait()), timeout=timeout)
    except TimeoutError:
        timed_out = True
        with contextlib.suppress(DockerError):
            await _capture(["kill", spec.name])
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=15)

    exit_code = None
    if not timed_out:
        with contextlib.suppress(DockerError):
            raw = await _capture(
                ["inspect", "--format", "{{.State.ExitCode}}", spec.name]
            )
            exit_code = int(raw.strip())

    await remove(spec.name, force=True)

    output = collected.decode(errors="replace")
    if len(collected) >= MAX_OUTPUT_BYTES:
        output = "… earlier output trimmed …\n" + output[-MAX_OUTPUT_BYTES:]

    return TaskResult(exit_code=exit_code, output=output, timed_out=timed_out)


async def list_process_containers(project_slug: str) -> list[str]:
    """Names of the worker containers currently running for a project."""
    out = await _capture(
        [
            "ps",
            "--all",
            "--format",
            "{{.Names}}",
            "--filter",
            f"label={PROJECT_LABEL}={project_slug}",
            "--filter",
            f"label={ROLE_LABEL}=worker",
        ]
    )
    # A draining container still carries the worker's labels, but it is on
    # its way out and no longer holds its old name. Counting it would make
    # every promotion drain it again and restart its clock.
    return [
        name
        for name in (line.strip() for line in out.splitlines())
        if name and draining_deadline(name) is None
    ]
