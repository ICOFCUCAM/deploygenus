#!/usr/bin/env bash
# Install or upgrade Forge on a fresh Ubuntu (22.04, 24.04) or Debian (12, 13)
# server. Run it as root:
#
#   FORGE_DEPLOY_DOMAIN=deploys.example.com \
#   FORGE_ACME_EMAIL=you@example.com \
#   CF_DNS_API_TOKEN=… \
#   bash scripts/install.sh
#
# Anything not given in the environment is asked for. Running it again later
# is an upgrade: it pulls the latest code, rebuilds, migrates and restarts,
# and never touches the secrets it generated the first time.
#
# What it does, in order:
#   1. installs Docker Engine and the compose plugin from Docker's own repo
#   2. puts Forge in /opt/forge (or updates it)
#   3. writes /opt/forge/.env with freshly generated secrets, mode 0600
#   4. checks that the deploy domain and its wildcard point at this server
#   5. opens ports 80 and 443 if a ufw firewall is active
#   6. builds and starts the stack, applies migrations, runs `forge doctor`
#   7. installs /usr/local/bin/forge, so `forge …` works from any shell
#
#   --env-only   stop after writing .env (to review it before starting)
set -Eeuo pipefail
trap 'echo "install failed on line $LINENO: $BASH_COMMAND" >&2' ERR

FORGE_HOME="${FORGE_HOME:-/opt/forge}"
FORGE_REPO="${FORGE_REPO:-https://github.com/ICOFCUCAM/deploygenus.git}"
FORGE_BRANCH="${FORGE_BRANCH:-main}"
ENV_ONLY=0
[ "${1:-}" = "--env-only" ] && ENV_ONLY=1

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die() {
    printf '\033[31merror:\033[0m %s\n' "$*" >&2
    exit 1
}

ask() {
    # ask VAR "question" [default] — keeps a value already in the environment.
    local var="$1" question="$2" default="${3:-}" answer
    [ -n "${!var:-}" ] && return
    [ -t 0 ] || die "$var is not set, and there is no terminal to ask on"
    read -r -p "$question${default:+ [$default]}: " answer
    printf -v "$var" '%s' "${answer:-$default}"
    [ -n "${!var}" ] || die "$var is required"
}

# A Fernet key is 32 random bytes, url-safe base64. Generated here rather than
# with `forge keygen` because on a first install nothing is built yet.
fernet_key() { openssl rand 32 | base64 | tr '+/' '-_' | tr -d '\n'; }

public_ip() {
    curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null ||
        curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || true
}

resolves_to() {
    # resolves_to <name> — the A records, space-separated.
    getent ahostsv4 "$1" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' '
}

# ---------------------------------------------------------------------------

[ "$ENV_ONLY" = 1 ] || [ "$(id -u)" = 0 ] || die "run as root (sudo -i first)"

if [ "$ENV_ONLY" = 0 ]; then
    say "1/7 Docker"
    if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
        note "already installed: $(docker --version)"
    else
        . /etc/os-release
        case "$ID" in ubuntu | debian) ;; *) die "this installer supports Ubuntu and Debian; you have $ID" ;; esac
        apt-get update -qq
        apt-get install -y -qq ca-certificates curl git openssl >/dev/null
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" \
            >/etc/apt/sources.list.d/docker.list
        apt-get update -qq
        apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
            docker-buildx-plugin docker-compose-plugin >/dev/null
        systemctl enable --now docker >/dev/null
        note "installed: $(docker --version)"
    fi

    say "2/7 Forge in $FORGE_HOME"
    if [ -d "$FORGE_HOME/.git" ]; then
        git -C "$FORGE_HOME" fetch -q origin "$FORGE_BRANCH"
        git -C "$FORGE_HOME" checkout -q "$FORGE_BRANCH"
        git -C "$FORGE_HOME" merge -q --ff-only "origin/$FORGE_BRANCH"
        note "updated to $(git -C "$FORGE_HOME" rev-parse --short HEAD)"
    else
        git clone -q --branch "$FORGE_BRANCH" "$FORGE_REPO" "$FORGE_HOME"
        note "cloned $(git -C "$FORGE_HOME" rev-parse --short HEAD)"
    fi
fi

cd "$FORGE_HOME"

say "3/7 configuration"
if [ -f .env ]; then
    note ".env exists — keeping it and its secrets"
else
    ask FORGE_DEPLOY_DOMAIN "Deploy domain (every deployment gets <id>.<this>)"
    ask FORGE_ACME_EMAIL "Email for Let's Encrypt"
    FORGE_DNS_PROVIDER="${FORGE_DNS_PROVIDER:-cloudflare}"
    if [ "$FORGE_DNS_PROVIDER" = cloudflare ]; then
        ask CF_DNS_API_TOKEN "Cloudflare API token (Zone > Zone > Read and Zone > DNS > Edit)"
    fi
    FORGE_DEPLOY_DOMAIN="$(echo "$FORGE_DEPLOY_DOMAIN" | tr 'A-Z' 'a-z' | sed 's/^\*\.//; s/\.$//')"
    POSTGRES_PASSWORD="$(openssl rand -hex 24)"
    umask 077
    cat >.env <<EOF
# Written by scripts/install.sh on $(date -u +%Y-%m-%d). See .env.example for
# what each setting does. The three secrets below were generated for this
# server and exist nowhere else.

FORGE_DEPLOY_DOMAIN=$FORGE_DEPLOY_DOMAIN
FORGE_API_TOKEN=$(openssl rand -hex 32)

# Encrypts every stored environment variable. NOT included in backups.
# Copy it into a password manager now: without it, a restored database's
# variables cannot be decrypted.
FORGE_MASTER_KEY=$(fernet_key)

FORGE_CERT_RESOLVER=le
FORGE_ACME_EMAIL=$FORGE_ACME_EMAIL
FORGE_DNS_PROVIDER=$FORGE_DNS_PROVIDER
CF_DNS_API_TOKEN=${CF_DNS_API_TOKEN:-}

POSTGRES_PASSWORD=$POSTGRES_PASSWORD
DATABASE_URL=postgresql://forge:$POSTGRES_PASSWORD@postgres:5432/forge

FORGE_NETWORK=forge
FORGE_BUILD_ROOT=/var/lib/forge/builds
FORGE_ROUTER_CONFIG_DIR=/var/lib/forge/router
ENVIRONMENT=production

FORGE_ALERT_WEBHOOK_URL=${FORGE_ALERT_WEBHOOK_URL:-}
FORGE_BACKUP_DIR=/var/backups/forge
FORGE_BACKUP_HOST_DIR=/var/backups/forge
FORGE_BACKUP_HOUR=3
FORGE_BACKUP_KEEP=7
EOF
    umask 022
    chmod 600 .env
    note "wrote .env (mode 600) with a new API token, master key and database password"
fi

# shellcheck disable=SC1091
set -a && . ./.env && set +a

if [ "$ENV_ONLY" = 1 ]; then
    note "--env-only: stopping here. Review .env, then run this again without it."
    exit 0
fi

say "4/7 DNS"
ip="$(public_ip)"
probe="check-$(openssl rand -hex 3).$FORGE_DEPLOY_DOMAIN"
bare="$(resolves_to "$FORGE_DEPLOY_DOMAIN")"
wild="$(resolves_to "$probe")"
if [ -z "$ip" ]; then
    note "could not work out this server's public IP; skipping the check"
elif [[ " $bare " == *" $ip "* && " $wild " == *" $ip "* ]]; then
    note "$FORGE_DEPLOY_DOMAIN and *.$FORGE_DEPLOY_DOMAIN both point here ($ip)"
else
    note "WARNING: DNS does not point at this server ($ip) yet:"
    note "  $FORGE_DEPLOY_DOMAIN      -> ${bare:-nothing}"
    note "  *.$FORGE_DEPLOY_DOMAIN    -> ${wild:-nothing}"
    note "Add both as A records to $ip. If your DNS is Cloudflare, set them to"
    note "'DNS only' (grey cloud): Forge terminates TLS itself."
    note "Carrying on — certificates are requested once DNS is right."
fi

say "5/7 firewall"
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
    note "ufw: opened 80 and 443"
else
    note "no active ufw — make sure your provider's firewall allows 80 and 443"
fi

say "6/7 starting Forge"
mkdir -p "${FORGE_BACKUP_HOST_DIR:-/var/backups/forge}"
docker compose up -d --build --remove-orphans
# Migrate until it works: it fails only while Postgres is still starting, and
# it is safe to repeat. The worker restarts itself until the schema exists.
migrated=0
for _ in $(seq 1 60); do
    if docker compose exec -T api forge migrate; then migrated=1 && break; fi
    sleep 2
done
[ "$migrated" = 1 ] || die "migrations did not apply — 'docker compose logs api postgres'"
docker compose exec -T api forge doctor || true

say "7/7 the forge command"
cat >/usr/local/bin/forge <<EOF
#!/bin/sh
# Installed by $FORGE_HOME/scripts/install.sh: runs the Forge CLI in the
# control plane container, with a terminal when there is one.
cd "$FORGE_HOME" || exit 1
if [ -t 0 ]; then exec docker compose exec api forge "\$@"; fi
exec docker compose exec -T api forge "\$@"
EOF
chmod 755 /usr/local/bin/forge
note "try: forge doctor"

cat <<EOF

$(printf '\033[32m')Forge is running.$(printf '\033[0m')

  Dashboard   https://forge.$FORGE_DEPLOY_DOMAIN
  Sign in     the FORGE_API_TOKEN in $FORGE_HOME/.env

  Now, before anything else:
  1. Copy FORGE_MASTER_KEY from $FORGE_HOME/.env into a password manager.
  2. Set FORGE_ALERT_WEBHOOK_URL in .env (Slack or Discord), then
     'docker compose up -d' and 'forge test-alert'.
  3. Copy /var/backups/forge off this server on a schedule (rclone, rsync).

  First deploy:
     forge project create --name "My app" --repo https://github.com/you/app.git
     forge deploy my-app

  Upgrade later by running this script again.
EOF
