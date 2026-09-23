#!/usr/bin/env bash
# Install or upgrade DeployPro on a fresh Ubuntu (22.04, 24.04) or Debian (12, 13)
# server. Run it as root:
#
#   DEPLOYPRO_DEPLOY_DOMAIN=deploys.example.com \
#   DEPLOYPRO_ACME_EMAIL=you@example.com \
#   CF_DNS_API_TOKEN=… \
#   bash scripts/install.sh
#
# Anything not given in the environment is asked for. Running it again later
# is an upgrade: it pulls the latest code, rebuilds, migrates and restarts,
# and never touches the secrets it generated the first time.
#
# What it does, in order:
#   1. installs Docker Engine and the compose plugin from Docker's own repo
#   2. puts DeployPro in /opt/deploypro (or updates it)
#   3. writes /opt/deploypro/.env with freshly generated secrets, mode 0600
#   4. checks that the deploy domain and its wildcard point at this server
#   5. opens ports 80 and 443 if a ufw firewall is active
#   6. builds and starts the stack, applies migrations, runs `deploypro doctor`
#   7. installs /usr/local/bin/deploypro, so `deploypro …` works from any shell
#
#   --env-only   stop after writing .env (to review it before starting)
set -Eeuo pipefail
trap 'echo "install failed on line $LINENO: $BASH_COMMAND" >&2' ERR

DEPLOYPRO_HOME="${DEPLOYPRO_HOME:-/opt/deploypro}"
DEPLOYPRO_REPO="${DEPLOYPRO_REPO:-https://github.com/ICOFCUCAM/deploygenus.git}"
DEPLOYPRO_BRANCH="${DEPLOYPRO_BRANCH:-main}"
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
# with `deploypro keygen` because on a first install nothing is built yet.
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

    say "2/7 DeployPro in $DEPLOYPRO_HOME"
    if [ -d "$DEPLOYPRO_HOME/.git" ]; then
        git -C "$DEPLOYPRO_HOME" fetch -q origin "$DEPLOYPRO_BRANCH"
        git -C "$DEPLOYPRO_HOME" checkout -q "$DEPLOYPRO_BRANCH"
        git -C "$DEPLOYPRO_HOME" merge -q --ff-only "origin/$DEPLOYPRO_BRANCH"
        note "updated to $(git -C "$DEPLOYPRO_HOME" rev-parse --short HEAD)"
    else
        git clone -q --branch "$DEPLOYPRO_BRANCH" "$DEPLOYPRO_REPO" "$DEPLOYPRO_HOME"
        note "cloned $(git -C "$DEPLOYPRO_HOME" rev-parse --short HEAD)"
    fi
fi

cd "$DEPLOYPRO_HOME"

say "3/7 configuration"
if [ -f .env ]; then
    note ".env exists — keeping it and its secrets"
else
    ask DEPLOYPRO_DEPLOY_DOMAIN "Deploy domain (every deployment gets <id>.<this>)"
    ask DEPLOYPRO_ACME_EMAIL "Email for Let's Encrypt"
    DEPLOYPRO_DNS_PROVIDER="${DEPLOYPRO_DNS_PROVIDER:-cloudflare}"
    if [ "$DEPLOYPRO_DNS_PROVIDER" = cloudflare ]; then
        ask CF_DNS_API_TOKEN "Cloudflare API token (Zone > Zone > Read and Zone > DNS > Edit)"
    fi
    DEPLOYPRO_DEPLOY_DOMAIN="$(echo "$DEPLOYPRO_DEPLOY_DOMAIN" | tr 'A-Z' 'a-z' | sed 's/^\*\.//; s/\.$//')"
    POSTGRES_PASSWORD="$(openssl rand -hex 24)"
    umask 077
    cat >.env <<EOF
# Written by scripts/install.sh on $(date -u +%Y-%m-%d). See .env.example for
# what each setting does. The three secrets below were generated for this
# server and exist nowhere else.

DEPLOYPRO_DEPLOY_DOMAIN=$DEPLOYPRO_DEPLOY_DOMAIN
DEPLOYPRO_API_TOKEN=$(openssl rand -hex 32)

# Encrypts every stored environment variable. NOT included in backups.
# Copy it into a password manager now: without it, a restored database's
# variables cannot be decrypted.
DEPLOYPRO_MASTER_KEY=$(fernet_key)

DEPLOYPRO_CERT_RESOLVER=le
DEPLOYPRO_ACME_EMAIL=$DEPLOYPRO_ACME_EMAIL
DEPLOYPRO_DNS_PROVIDER=$DEPLOYPRO_DNS_PROVIDER
CF_DNS_API_TOKEN=${CF_DNS_API_TOKEN:-}

POSTGRES_PASSWORD=$POSTGRES_PASSWORD
DATABASE_URL=postgresql://deploypro:$POSTGRES_PASSWORD@postgres:5432/deploypro

DEPLOYPRO_NETWORK=deploypro
DEPLOYPRO_BUILD_ROOT=/var/lib/deploypro/builds
DEPLOYPRO_ROUTER_CONFIG_DIR=/var/lib/deploypro/router
ENVIRONMENT=production

DEPLOYPRO_ALERT_WEBHOOK_URL=${DEPLOYPRO_ALERT_WEBHOOK_URL:-}
DEPLOYPRO_BACKUP_DIR=/var/backups/deploypro
DEPLOYPRO_BACKUP_HOST_DIR=/var/backups/deploypro
DEPLOYPRO_BACKUP_HOUR=3
DEPLOYPRO_BACKUP_KEEP=7
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
probe="check-$(openssl rand -hex 3).$DEPLOYPRO_DEPLOY_DOMAIN"
bare="$(resolves_to "$DEPLOYPRO_DEPLOY_DOMAIN")"
wild="$(resolves_to "$probe")"
if [ -z "$ip" ]; then
    note "could not work out this server's public IP; skipping the check"
elif [[ " $bare " == *" $ip "* && " $wild " == *" $ip "* ]]; then
    note "$DEPLOYPRO_DEPLOY_DOMAIN and *.$DEPLOYPRO_DEPLOY_DOMAIN both point here ($ip)"
else
    note "WARNING: DNS does not point at this server ($ip) yet:"
    note "  $DEPLOYPRO_DEPLOY_DOMAIN      -> ${bare:-nothing}"
    note "  *.$DEPLOYPRO_DEPLOY_DOMAIN    -> ${wild:-nothing}"
    note "Add both as A records to $ip. If your DNS is Cloudflare, set them to"
    note "'DNS only' (grey cloud): DeployPro terminates TLS itself."
    note "Carrying on — certificates are requested once DNS is right."
fi

say "5/7 firewall"
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
    note "ufw: opened 80 and 443"
else
    note "no active ufw — make sure your provider's firewall allows 80 and 443"
fi

say "6/7 starting DeployPro"
mkdir -p "${DEPLOYPRO_BACKUP_HOST_DIR:-/var/backups/deploypro}"
docker compose up -d --build --remove-orphans
# Migrate until it works: it fails only while Postgres is still starting, and
# it is safe to repeat. The worker restarts itself until the schema exists.
migrated=0
for _ in $(seq 1 60); do
    if docker compose exec -T api deploypro migrate; then migrated=1 && break; fi
    sleep 2
done
[ "$migrated" = 1 ] || die "migrations did not apply — 'docker compose logs api postgres'"
docker compose exec -T api deploypro doctor || true

say "7/7 the deploypro command"
cat >/usr/local/bin/deploypro <<EOF
#!/bin/sh
# Installed by $DEPLOYPRO_HOME/scripts/install.sh: runs the DeployPro CLI in the
# control plane container, with a terminal when there is one.
cd "$DEPLOYPRO_HOME" || exit 1
if [ -t 0 ]; then exec docker compose exec api deploypro "\$@"; fi
exec docker compose exec -T api deploypro "\$@"
EOF
chmod 755 /usr/local/bin/deploypro
note "try: deploypro doctor"

cat <<EOF

$(printf '\033[32m')DeployPro is running.$(printf '\033[0m')

  Dashboard   https://deploypro.$DEPLOYPRO_DEPLOY_DOMAIN
  Sign in     the DEPLOYPRO_API_TOKEN in $DEPLOYPRO_HOME/.env

  Now, before anything else:
  1. Copy DEPLOYPRO_MASTER_KEY from $DEPLOYPRO_HOME/.env into a password manager.
  2. Set DEPLOYPRO_ALERT_WEBHOOK_URL in .env (Slack or Discord), then
     'docker compose up -d' and 'deploypro test-alert'.
  3. Copy /var/backups/deploypro off this server on a schedule (rclone, rsync).

  First deploy:
     deploypro project create --name "My app" --repo https://github.com/you/app.git
     deploypro deploy my-app

  Upgrade later by running this script again.
EOF
