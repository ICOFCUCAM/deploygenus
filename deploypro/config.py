"""Runtime configuration, read once from the environment.

Same rule as the sibling project: no secret has a default. A missing secret
fails at startup rather than silently degrading to an insecure mode at request
time — which for this service would mean deploying containers with an empty
encryption key and writing unencrypted environment variables to disk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is required but not set")
    return value


def _optional(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str

    #: Fernet key protecting every stored environment variable. Rotating it
    #: without re-encrypting makes every existing variable unreadable, which is
    #: why it is named a master key rather than a password.
    master_key: str

    #: Bearer token for the control plane API. One user, one token — the
    #: audience decision from day one, and the reason there is no login screen.
    api_token: str

    #: Every deployment gets `<short-id>.<deploy_domain>` for as long as it
    #: exists. A wildcard DNS record and a wildcard certificate cover all of
    #: them at once, so a new deployment needs no DNS work and no ACME round
    #: trip — it is addressable the moment its container is up.
    deploy_domain: str

    #: The Docker network the router and every deployment share. The router
    #: reaches containers on it by name, so no deployment ever publishes a
    #: port on the host.
    network: str

    #: Where repositories are cloned and build contexts are assembled. Must be
    #: writable, and on the same filesystem Docker builds from.
    build_root: Path

    #: The directory the edge router watches for dynamic configuration. Every
    #: promotion writes one file here. Shared with the router container, which
    #: only ever reads it.
    router_config_dir: Path

    #: Name of the Traefik certificate resolver to attach to public routes.
    #: Empty disables TLS entirely, which is what local development wants and
    #: what production must never have.
    cert_resolver: str

    #: A build that has not finished by now is not going to. Kills the builder
    #: and fails the deployment rather than holding the worker forever.
    build_timeout_seconds: int

    #: How long a freshly started container has to answer its health check
    #: before the deployment is declared failed and the container destroyed.
    health_timeout_seconds: int
    health_path: str

    environment: str
    pool_min_size: int
    pool_max_size: int

    # -- housekeeping -------------------------------------------------------
    # Everything below is optional, with a default that is safe to run with.

    #: Where alerts go: a Slack or Discord incoming-webhook URL, or anything
    #: that accepts a JSON POST. Empty sends nothing.
    alert_webhook_url: str = ""

    #: Images kept per project besides production's and the warm ones: the
    #: rollback targets that restart in seconds. Older deployments stay listed
    #: and can be redeployed, which rebuilds them.
    keep_images: int = 10

    #: Build logs and job runs older than this are deleted, except the build
    #: log of whatever is serving production.
    log_retention_days: int = 30

    #: Alert when the disk holding the build root is fuller than this.
    disk_alert_percent: int = 90

    #: Seconds between checks of every production site and worker. A site is
    #: reported down after two failed checks in a row.
    monitor_interval_seconds: int = 60

    #: Where the daily backup is written. Empty turns scheduled backups off;
    #: `deploypro backup --dest` still works by hand.
    backup_dir: Path | None = None
    #: The hour (UTC) the daily backup runs at, and how many are kept.
    backup_hour: int = 3
    backup_keep: int = 7

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def tls_enabled(self) -> bool:
        return bool(self.cert_resolver)

    @property
    def scheme(self) -> str:
        return "https" if self.tls_enabled else "http"

    def deployment_host(self, short_id: str) -> str:
        return f"{short_id}.{self.deploy_domain}"

    def deployment_url(self, short_id: str) -> str:
        return f"{self.scheme}://{self.deployment_host(short_id)}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    environment = _optional("ENVIRONMENT", "development")
    cert_resolver = _optional("DEPLOYPRO_CERT_RESOLVER", "")

    if environment == "production" and not cert_resolver:
        # Serving customer sites over plaintext is not a degraded mode worth
        # having. In development it is the only sane default; in production it
        # is a misconfiguration that should never reach a request.
        raise ConfigError(
            "DEPLOYPRO_CERT_RESOLVER must be set in production — "
            "refusing to serve deployments without TLS"
        )

    return Settings(
        database_url=_required("DATABASE_URL"),
        master_key=_required("DEPLOYPRO_MASTER_KEY"),
        api_token=_required("DEPLOYPRO_API_TOKEN"),
        deploy_domain=_required("DEPLOYPRO_DEPLOY_DOMAIN")
        .lower()
        .lstrip("*.")
        .strip("."),
        network=_optional("DEPLOYPRO_NETWORK", "deploypro"),
        build_root=Path(_optional("DEPLOYPRO_BUILD_ROOT", "/var/lib/deploypro/builds")),
        router_config_dir=Path(
            _optional("DEPLOYPRO_ROUTER_CONFIG_DIR", "/var/lib/deploypro/router")
        ),
        cert_resolver=cert_resolver,
        build_timeout_seconds=_int("DEPLOYPRO_BUILD_TIMEOUT", 1800),
        health_timeout_seconds=_int("DEPLOYPRO_HEALTH_TIMEOUT", 90),
        health_path=_optional("DEPLOYPRO_HEALTH_PATH", "/"),
        environment=environment,
        pool_min_size=_int("DEPLOYPRO_POOL_MIN", 1),
        pool_max_size=_int("DEPLOYPRO_POOL_MAX", 8),
        alert_webhook_url=_optional("DEPLOYPRO_ALERT_WEBHOOK_URL", ""),
        keep_images=max(_int("DEPLOYPRO_KEEP_IMAGES", 10), 0),
        log_retention_days=max(_int("DEPLOYPRO_LOG_RETENTION_DAYS", 30), 1),
        disk_alert_percent=min(max(_int("DEPLOYPRO_DISK_ALERT_PERCENT", 90), 1), 100),
        monitor_interval_seconds=max(_int("DEPLOYPRO_MONITOR_INTERVAL", 60), 1),
        backup_dir=(
            Path(os.environ["DEPLOYPRO_BACKUP_DIR"])
            if os.environ.get("DEPLOYPRO_BACKUP_DIR")
            else None
        ),
        backup_hour=_int("DEPLOYPRO_BACKUP_HOUR", 3) % 24,
        backup_keep=max(_int("DEPLOYPRO_BACKUP_KEEP", 7), 1),
    )
