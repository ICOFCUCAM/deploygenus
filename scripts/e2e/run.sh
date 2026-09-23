#!/usr/bin/env bash
# End to end, against a real Docker daemon, a real Traefik and a real Postgres.
#
# What the unit suite cannot prove, this does: that a push is built, started,
# health-checked and routed; that production domains move on a promotion and
# back on a rollback; that a volume survives deploys and is shared by the web
# process, the worker and a cron job but never a preview; that a replaced
# worker finishes the render in hand, and is killed when its stop timeout
# runs out; and that a signed webhook deploys while a forged one does not.
#
# It needs: a running Docker daemon, go, git, openssl, curl, psql, and either
# DATABASE_URL pointing at an EMPTY database or Postgres server binaries (it
# starts a throwaway cluster). Nothing is pulled from a registry except the
# Traefik image, which is built from the GitHub release if it cannot be pulled.
#
# Everything it creates is namespaced (`deploypro-e2e` network, `e2e-bv` project,
# its own ports) and removed at the end, so it can run on a host that also
# runs DeployPro for real. Set KEEP=1 to leave it all running for a look around.
#
#   ./scripts/e2e/run.sh
set -Eeuo pipefail
# Any command failing outside a check still says where, instead of the run
# just stopping: an end-to-end run that can end silently cannot be trusted.
trap 'fail "unexpected error on line $LINENO: $BASH_COMMAND"' ERR

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
VENV="${VENV:-$REPO/.venv}"
DEPLOYPRO="$VENV/bin/deploypro"
HTTP_PORT="${E2E_HTTP_PORT:-18080}"
GIT_PORT="${E2E_GIT_PORT:-18443}"
SINK_PORT="${E2E_SINK_PORT:-18090}"
SSH_PORT="${E2E_SSH_PORT:-18022}"
GH_PORT="${E2E_GH_PORT:-18444}"
PG_PORT="${E2E_PG_PORT:-55433}"
TRAEFIK_IMAGE="${TRAEFIK_IMAGE:-traefik:v3.7}"
TRAEFIK_RELEASE="${TRAEFIK_RELEASE:-v3.7.13}"
SLUG=e2e-bv
GH_SLUG=e2e-gh
DOMAIN=e2e-bv.test
WORK="$(mktemp -d /var/tmp/deploypro-e2e.XXXXXX)"
PIDS=()
PASSED=0

# -- output -----------------------------------------------------------------

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
pass() { PASSED=$((PASSED + 1)); printf '  \033[32mok\033[0m   %s\n' "$*"; }
fail() {
    printf '  \033[31mFAIL\033[0m %s\n' "$*"
    printf '\n-- last worker log lines --\n'
    tail -25 "$WORK/worker.log" 2>/dev/null || true
    exit 1
}

# `check <description> <command…>` — pass if the command succeeds.
check() {
    local what="$1"
    shift
    if "$@" >/dev/null 2>&1; then pass "$what"; else fail "$what"; fi
}

# `wait_for <seconds> <description> <command…>` — pass once the command succeeds.
wait_for() {
    local seconds="$1" what="$2"
    shift 2
    local deadline=$((SECONDS + seconds))
    until "$@" >/dev/null 2>&1; do
        [ "$SECONDS" -ge "$deadline" ] && fail "$what (waited ${seconds}s)"
        sleep 1
    done
    pass "$what"
}

# -- helpers ----------------------------------------------------------------

get() { curl -sS --max-time 5 -H "Host: $1" "http://127.0.0.1:$HTTP_PORT$2"; }
serves() { get "$1" / | grep -q -- "$2"; }
uploads() { get "$1" "/upload?name=$2" | grep -q ok; }
logged() { get "$DOMAIN" /events | grep -q -- "$1"; }
lacks() { ! get "$1" / | grep -q -- "$2"; }
sql() { psql "$DATABASE_URL" -tAc "$1"; }
deployment_host() { echo "$(sql "select short_id from deployments where number = $1").deploys.test"; }
latest_status() { sql "select status from deployments order by number desc limit 1"; }
draining() { docker ps --format '{{.Names}}' --filter "label=deploypro.project=$SLUG" | grep -q '\.draining\.'; }
alerted() { grep -q -- "$1" "$WORK/alerts.log" 2>/dev/null; }
web_container() { sql "select container_id from deployments d join projects p on p.production_deployment_id = d.id"; }
deploypro_images() { docker images -q --filter "label=deploypro.instance=deploypro-e2e" | sort -u; }
no_draining() { ! docker ps -a --format '{{.Names}}' --filter "label=deploypro.project=$SLUG" | grep -q '\.draining\.'; }

# `deploy_expecting_failure` — queue a deploy and wait for it to fail.
deploy_expecting_failure() {
    "$DEPLOYPRO" deploy "$SLUG" >/dev/null
    local deadline=$((SECONDS + 120))
    until [ "$(latest_status)" = failed ]; do
        [ "$(latest_status)" = ready ] && fail "a deploy that should have failed went live"
        [ "$SECONDS" -ge "$deadline" ] && fail "deploy did not finish in 120s"
        sleep 1
    done
}

# `deploy [--ref branch]` — queue a deploy and wait until it is ready and,
# for the production branch, until production has moved to it: promotion runs
# after "ready", and a check made in between sees the previous deployment.
deploy() {
    "$DEPLOYPRO" deploy "$SLUG" "$@" >/dev/null
    local deadline=$((SECONDS + 120))
    while true; do
        case "$(latest_status)" in
        ready)
            [ "$#" -gt 0 ] && return 0 # a preview is never promoted
            [ "$(sql "select p.production_deployment_id = d.id from projects p, deployments d order by d.number desc limit 1")" = t ] && return 0
            ;;
        failed) fail "deploy failed: $(sql "select error from deployments order by number desc limit 1")" ;;
        esac
        [ "$SECONDS" -ge "$deadline" ] && fail "deploy did not finish in 120s"
        sleep 1
    done
}

commit_version() {
    local version="$1" branch="${2:-main}"
    (
        cd "$WORK/src"
        git checkout -q "$branch" 2>/dev/null || git checkout -qb "$branch"
        CGO_ENABLED=0 go build -ldflags "-X main.version=$version" -o app .
        git add -A
        git -c user.email=e2e@deploypro -c user.name=e2e commit -qm "$version"
        git push -q "$WORK/git/app.git" "$branch"
        git checkout -q main
    )
}

restart_worker() {
    [ -n "${WORKER_PID:-}" ] && kill "$WORKER_PID" 2>/dev/null && wait "$WORKER_PID" 2>/dev/null || true
    "$VENV/bin/python" -m deploypro.worker >>"$WORK/worker.log" 2>&1 &
    WORKER_PID=$!
    PIDS+=("$WORKER_PID")
}

cleanup() {
    local status=$?
    if [ "${KEEP:-0}" = 1 ]; then
        echo "KEEP=1 — left running; work directory $WORK"
        return
    fi
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    for project in "$SLUG" "$GH_SLUG"; do
        docker ps -aq --filter "label=deploypro.project=$project" | xargs -r docker rm -f >/dev/null 2>&1 || true
        docker images -q "deploypro/$project" | sort -u | xargs -r docker rmi -f >/dev/null 2>&1 || true
    done
    docker rm -f deploypro-e2e-router >/dev/null 2>&1 || true
    docker rm -f deploypro-e2e-orphan deploypro-e2e-foreign >/dev/null 2>&1 || true
    docker network rm deploypro-e2e-other >/dev/null 2>&1 || true
    docker volume ls -q --filter "label=deploypro.project=$SLUG" | xargs -r docker volume rm >/dev/null 2>&1 || true
    docker images -q "deploypro/$SLUG" | sort -u | xargs -r docker rmi -f >/dev/null 2>&1 || true
    docker network rm deploypro-e2e >/dev/null 2>&1 || true
    if [ -n "${PG_BIN:-}" ]; then
        run_pg "$PG_BIN/pg_ctl" -D "$WORK/pg/data" -m immediate stop >/dev/null 2>&1 || true
    fi
    rm -rf "$WORK"
    exit "$status"
}
trap cleanup EXIT

run_pg() {
    # initdb refuses to run as root.
    if [ "$(id -u)" = 0 ]; then su postgres -s /bin/sh -c "$(printf '%q ' "$@")"; else "$@"; fi
}

# ---------------------------------------------------------------------------

step "setting up in $WORK"

docker info >/dev/null 2>&1 || fail "no Docker daemon"
for tool in go git openssl curl psql; do
    command -v "$tool" >/dev/null || fail "$tool is not installed"
done

if [ -z "${DATABASE_URL:-}" ]; then
    PG_BIN="${PG_BIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
    [ -x "$PG_BIN/initdb" ] || fail "set DATABASE_URL, or install Postgres server binaries"
    mkdir -p "$WORK/pg"
    [ "$(id -u)" = 0 ] && chown postgres "$WORK" "$WORK/pg"
    run_pg "$PG_BIN/initdb" -D "$WORK/pg/data" -A trust -U deploypro >/dev/null
    run_pg "$PG_BIN/pg_ctl" -D "$WORK/pg/data" -l "$WORK/pg/log" \
        -o "-p $PG_PORT -k $WORK/pg -c listen_addresses=127.0.0.1" start >/dev/null
    psql -h 127.0.0.1 -p "$PG_PORT" -U deploypro -d postgres -qc "create database deploypro"
    export DATABASE_URL="postgresql://deploypro@127.0.0.1:$PG_PORT/deploypro"
fi

DEPLOYPRO_MASTER_KEY="$("$DEPLOYPRO" keygen)"
export DEPLOYPRO_MASTER_KEY
export DEPLOYPRO_API_TOKEN=e2e-token
export DEPLOYPRO_DEPLOY_DOMAIN=deploys.test
export DEPLOYPRO_CERT_RESOLVER=
export ENVIRONMENT=development
export DEPLOYPRO_NETWORK=deploypro-e2e
export DEPLOYPRO_BUILD_ROOT="$WORK/build"
export DEPLOYPRO_ROUTER_CONFIG_DIR="$WORK/router"
export DEPLOYPRO_HEALTH_TIMEOUT=60
export DEPLOYPRO_ALERT_WEBHOOK_URL="http://127.0.0.1:$SINK_PORT/hook"
export DEPLOYPRO_MONITOR_INTERVAL=2
export DEPLOYPRO_KEEP_IMAGES=2
mkdir -p "$DEPLOYPRO_BUILD_ROOT" "$DEPLOYPRO_ROUTER_CONFIG_DIR"
"$DEPLOYPRO" migrate >/dev/null
pass "migrations applied"

# The router, as docker-compose.yml runs it, minus TLS.
if ! docker image inspect "$TRAEFIK_IMAGE" >/dev/null 2>&1 &&
    ! docker pull -q "$TRAEFIK_IMAGE" >/dev/null 2>&1; then
    mkdir -p "$WORK/traefik"
    curl -fsSL "https://github.com/traefik/traefik/releases/download/$TRAEFIK_RELEASE/traefik_${TRAEFIK_RELEASE}_linux_amd64.tar.gz" |
        tar xz -C "$WORK/traefik" traefik
    printf 'FROM scratch\nCOPY traefik /traefik\nENTRYPOINT ["/traefik"]\n' >"$WORK/traefik/Dockerfile"
    docker build -q -t "$TRAEFIK_IMAGE" "$WORK/traefik" >/dev/null
fi
docker network create deploypro-e2e >/dev/null
docker run -d --name deploypro-e2e-router --network deploypro-e2e -p "127.0.0.1:$HTTP_PORT:80" \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v "$DEPLOYPRO_ROUTER_CONFIG_DIR:/etc/traefik/dynamic:ro" "$TRAEFIK_IMAGE" \
    --providers.docker=true --providers.docker.exposedByDefault=false \
    --providers.docker.network=deploypro-e2e \
    --providers.file.directory=/etc/traefik/dynamic --providers.file.watch=true \
    --entrypoints.web.address=:80 >/dev/null
wait_for 20 "router is answering" get nothing.deploys.test /
check "router talks to this Docker daemon" \
    bash -c "! docker logs deploypro-e2e-router 2>&1 | grep -q 'client version .* is too old'"

# The test repository, served over HTTPS because DeployPro refuses file://.
mkdir -p "$WORK/git"
cp -r "$HERE/app" "$WORK/src"
(cd "$WORK/src" && git init -q -b main . && git add -A &&
    git -c user.email=e2e@deploypro -c user.name=e2e commit -qm init)
git clone -q --bare "$WORK/src" "$WORK/git/app.git"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=127.0.0.1" \
    -addext "subjectAltName=IP:127.0.0.1" \
    -keyout "$WORK/git/key.pem" -out "$WORK/git/cert.pem" 2>/dev/null
cat "$WORK/git/cert.pem" >"$WORK/git/ca.pem"
for bundle in /root/.ccr/ca-bundle.crt /etc/ssl/certs/ca-certificates.crt; do
    [ -r "$bundle" ] && cat "$bundle" >>"$WORK/git/ca.pem"
done
export GIT_SSL_CAINFO="$WORK/git/ca.pem"
python3 "$HERE/gitserver.py" "$WORK/git" "$GIT_PORT" "$WORK/git/cert.pem" "$WORK/git/key.pem" &
PIDS+=($!)
wait_for 10 "git server is answering" git ls-remote "https://127.0.0.1:$GIT_PORT/app.git"

python3 "$HERE/alertsink.py" "$SINK_PORT" "$WORK/alerts.log" &
PIDS+=($!)

# A stand-in for GitHub: its API and a private repository behind app tokens.
"$VENV/bin/python" "$HERE/fakegithub.py" "$WORK/git" "$GH_PORT" \
    "$WORK/git/cert.pem" "$WORK/git/key.pem" "$WORK/gh-secret" &
PIDS+=($!)
export DEPLOYPRO_GITHUB_URL="https://127.0.0.1:$GH_PORT"
export DEPLOYPRO_GITHUB_API_URL="https://127.0.0.1:$GH_PORT/api"
export SSL_CERT_FILE="$WORK/git/ca.pem" # how httpx is told to trust it
wait_for 10 "the stand-in GitHub is answering" \
    curl -s --cacert "$WORK/git/ca.pem" "https://127.0.0.1:$GH_PORT/api/nothing"

restart_worker

# ---------------------------------------------------------------------------

step "first deploy, with a volume"
"$DEPLOYPRO" project create --name "E2E BalanceVid" --slug "$SLUG" \
    --repo "https://127.0.0.1:$GIT_PORT/app.git" >/dev/null
"$DEPLOYPRO" volume add "$SLUG" recordings /data >/dev/null
"$DEPLOYPRO" project set "$SLUG" --stop-timeout 120 >/dev/null
commit_version v1
deploy
V1="$(deployment_host 1)"
wait_for 15 "deployment #1 serves on its own URL through the router" serves "$V1" "version=v1"
check "the app can write to the volume" uploads "$V1" wedding

step "production domain, worker and cron"
"$DEPLOYPRO" domain add "$SLUG" "$DOMAIN" --primary >/dev/null
sql "update domains set verified_at = now()" >/dev/null # DNS cannot be checked here
"$DEPLOYPRO" process add "$SLUG" render --type worker --command worker >/dev/null
"$DEPLOYPRO" process add "$SLUG" tidy --type cron --command job --schedule "* * * * *" \
    --timeout 30 >/dev/null
commit_version v2
deploy
check "the route file has an extension Traefik loads" test -f "$DEPLOYPRO_ROUTER_CONFIG_DIR/project-$SLUG.yml"
wait_for 15 "the production domain serves v2" serves "$DOMAIN" "version=v2"
wait_for 15 "the worker started" logged "v2 worker started"
wait_for 45 "the worker rendered the recording from before it existed" \
    serves "$DOMAIN" wedding.mp4

step "a deploy in the middle of a render"
check "a recording was uploaded" uploads "$DOMAIN" birthday
wait_for 10 "the worker is rendering" logged "v2 worker rendering birthday"
commit_version v3
started=$SECONDS
deploy
check "the deploy did not wait for the render ($((SECONDS - started))s)" test $((SECONDS - started)) -lt 20
wait_for 10 "the old worker was renamed and told to stop" draining
wait_for 10 "the old worker kept going" logged "mid-render of birthday, finishing it first"
wait_for 10 "the new worker started alongside it" logged "v3 worker started"
wait_for 40 "the old worker finished the render" serves "$DOMAIN" birthday.mp4
wait_for 20 "the finished container was cleaned up" no_draining

step "rollback"
started=$SECONDS
"$DEPLOYPRO" promote "$SLUG" '#1' >/dev/null
check "rollback took under 10s ($((SECONDS - started))s)" test $((SECONDS - started)) -lt 10
wait_for 10 "the production domain serves v1 again" serves "$DOMAIN" "version=v1"
check "every file survived" serves "$DOMAIN" "birthday.mp4,events.log,wedding.mp4"
"$DEPLOYPRO" promote "$SLUG" '#3' >/dev/null
wait_for 10 "and forward to v3" serves "$DOMAIN" "version=v3"

step "a preview never sees production's files"
commit_version preview feature
deploy --ref feature
PREVIEW="$(deployment_host "$(sql "select max(number) from deployments")")"
wait_for 15 "the preview serves the branch" serves "$PREVIEW" "version=preview"
check "the preview has no recordings" lacks "$PREVIEW" .mp4
check "production was not touched" serves "$DOMAIN" "version=v3"

step "a cron job sees the volume"
wait_for 90 "a scheduled run succeeded" bash -c "psql '$DATABASE_URL' -tAc \"select 1 from job_runs where status = 'succeeded'\" | grep -q 1"
# The job's line lands in events.log, which lives in the volume and is read
# back here through the web process: one file written by one container and
# read by another.
check "and it wrote into the same volume the web process reads" logged "job ran and saw"

step "a stop timeout is enforced"
"$DEPLOYPRO" project set "$SLUG" --stop-timeout 5 >/dev/null
echo 120s | "$DEPLOYPRO" env set "$SLUG" RENDER_TIME - --target production >/dev/null
commit_version v4
deploy
wait_for 15 "the v4 worker started" logged "v4 worker started"
check "a recording was uploaded" uploads "$DOMAIN" epic
wait_for 10 "a two-minute render started" logged "v4 worker rendering epic"
commit_version v5
deploy
wait_for 10 "the old worker was told to stop" draining
started=$SECONDS
wait_for 30 "it was killed at its deadline" no_draining
check "within the 5s timeout plus one 5s sweep ($((SECONDS - started))s)" test $((SECONDS - started)) -le 12
check "the worker log says so" grep -q "still running at the end of their stop timeout" "$WORK/worker.log"

step "webhooks"
"$VENV/bin/uvicorn" deploypro.main:app --host 127.0.0.1 --port 18000 >"$WORK/api.log" 2>&1 &
PIDS+=($!)
wait_for 15 "the API is up" curl -sf http://127.0.0.1:18000/ready
commit_version v6-webhook
SHA="$(git -C "$WORK/src" rev-parse HEAD)"
BODY="{\"ref\":\"refs/heads/main\",\"after\":\"$SHA\",\"head_commit\":{\"id\":\"$SHA\",\"message\":\"v6\",\"author\":{\"name\":\"e2e\"}}}"
SECRET="$(sql "select webhook_secret from projects where slug = '$SLUG'")"
SIG="$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $NF}')"
hook() {
    curl -s -o /dev/null -w '%{http_code}' -X POST -H "X-GitHub-Event: push" \
        -H "X-Hub-Signature-256: sha256=$1" -H "Content-Type: application/json" \
        -d "$BODY" "http://127.0.0.1:18000/webhooks/$SLUG"
}
check "a forged signature is refused" test "$(hook deadbeef)" = 401
check "a signed push is accepted" test "$(hook "$SIG")" = 202
wait_for 90 "and deployed" serves "$DOMAIN" "version=v6-webhook"

step "alerts"
"$DEPLOYPRO" test-alert >/dev/null
wait_for 10 "a test alert reaches the webhook" alerted "Test alert"

PROD="$(web_container)"
docker stop -t 1 "$PROD" >/dev/null
wait_for 30 "a stopped production site is reported down" alerted "E2E BalanceVid is down"
docker start "$PROD" >/dev/null
wait_for 30 "and reported back when it serves again" alerted "E2E BalanceVid is serving again"
check "a site that is down is reported once, not every check" \
    test "$(grep -c 'E2E BalanceVid is down' "$WORK/alerts.log")" = 1

docker stop -t 1 "deploypro-$SLUG-render-0" >/dev/null
wait_for 30 "a stopped worker is reported" alerted "worker render is not running"
docker start "deploypro-$SLUG-render-0" >/dev/null
wait_for 30 "and its recovery" alerted "worker render is running again"

commit_version crash
deploy_expecting_failure
wait_for 10 "a failed production deploy is reported" alerted "failed"
check "and production kept serving the last good version" serves "$DOMAIN" "version=v6-webhook"
check "the stop-timeout kill earlier was reported too" alerted "killed at the end of their stop timeout"

step "cleaning up old images"
before="$(deploypro_images | wc -l)"
"$DEPLOYPRO" housekeeping >"$WORK/housekeeping.log"
after="$(deploypro_images | wc -l)"
check "old images were removed ($before -> $after)" test "$after" -lt "$before"
check "every cleanup step ran without an error" bash -c "! grep -q error '$WORK/housekeeping.log'"
check "including the log and job-run retention queries" grep -q "job runs deleted" "$WORK/housekeeping.log"
check "production's image survived" \
    docker image inspect "$(sql "select image_tag from deployments d join projects p on p.production_deployment_id = d.id")"
check "production still serves" serves "$DOMAIN" "version=v6-webhook"
"$DEPLOYPRO" promote "$SLUG" '#1' >"$WORK/promote.log" 2>&1 || true
check "rolling back to a removed image says to redeploy instead" \
    grep -q "Redeploy the commit instead" "$WORK/promote.log"

step "backup, and a restore after losing the volume"
"$DEPLOYPRO" backup --dest "$WORK/backups" >"$WORK/backup.log"
BACKUP="$(ls -d "$WORK"/backups/deploypro-* | tail -1)"
check "a backup was written" test -f "$BACKUP/manifest.json"
check "it has the database" test -s "$BACKUP/deploypro.dump"
check "it has the volume, with the recordings in it" \
    bash -c "tar -tzf '$BACKUP/volumes/deploypro_${SLUG}_recordings.tar.gz' | grep -q 'data/wedding.mp4'"
check "it does not have the master key" bash -c "! grep -rq '$DEPLOYPRO_MASTER_KEY' '$BACKUP'"

# The disaster: every container of the project and the volume, gone.
docker ps -aq --filter "label=deploypro.project=$SLUG" | xargs -r docker rm -f >/dev/null
docker volume rm "deploypro_${SLUG}_recordings" >/dev/null
check "the volume is really gone" bash -c "! docker volume inspect deploypro_${SLUG}_recordings"
"$DEPLOYPRO" restore-volume "$BACKUP" "$SLUG" recordings >/dev/null
commit_version v7-restored
deploy
wait_for 20 "after restoring and deploying, the site is back" serves "$DOMAIN" "version=v7-restored"
check "with the first recording" serves "$DOMAIN" "wedding.mp4"
check "and the one rendered during a deploy" serves "$DOMAIN" "birthday.mp4"
check "and the app can still write there (ownership survived)" uploads "$DOMAIN" after-restore

psql "$DATABASE_URL" -qc "create database deploypro_restored"
RESTORED="${DATABASE_URL%/*}/deploypro_restored"
pg_restore --no-owner -d "$RESTORED" "$BACKUP/deploypro.dump"
check "the database dump restores into an empty database" \
    test "$(psql "$RESTORED" -tAc "select count(*) from deployments")" -ge 8
check "with the project, its volume and its encrypted variables" \
    test "$(psql "$RESTORED" -tAc "select count(*) from projects p join volumes v on v.project_id = p.id join env_vars e on e.project_id = p.id")" = 1

step "a private repository, over SSH with a deploy key"
if ! command -v sshd >/dev/null || ! command -v git-shell >/dev/null; then
    printf '  skip  sshd or git-shell not installed (apt-get install openssh-server)\n'
else
    # A stand-in for GitHub: sshd on localhost, accepting only keys listed in
    # its own authorized_keys, and only for git (git-shell), never a shell.
    mkdir -p "$WORK/sshd" /run/sshd
    ssh-keygen -q -t ed25519 -N '' -f "$WORK/sshd/host_key"
    : >"$WORK/sshd/authorized_keys"
    cat >"$WORK/sshd/config" <<SSHD
Port $SSH_PORT
ListenAddress 127.0.0.1
HostKey $WORK/sshd/host_key
AuthorizedKeysFile $WORK/sshd/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
StrictModes no
UsePAM no
PidFile $WORK/sshd/pid
SSHD
    start_sshd() {
        "$(command -v sshd)" -f "$WORK/sshd/config" -D -e 2>>"$WORK/sshd/log" &
        SSHD_PID=$!
        PIDS+=("$SSHD_PID")
    }
    start_sshd
    wait_for 10 "a local SSH git server is up" \
        bash -c "echo >/dev/tcp/127.0.0.1/$SSH_PORT"
    SSH_URL="ssh://$(id -un)@127.0.0.1:$SSH_PORT$WORK/git/app.git"
    sql "update projects set repo_url = '$SSH_URL' where slug = '$SLUG'" >/dev/null
    commit_version v8-private

    check "without a deploy key, the repository refuses DeployPro" \
        bash -c "! '$DEPLOYPRO' deploy '$SLUG' >'$WORK/nokey.log' 2>&1"
    check "and says why" grep -q "Permission denied" "$WORK/nokey.log"

    "$DEPLOYPRO" project key "$SLUG" >"$WORK/key.log"
    PUBLIC="$(grep '^ssh-ed25519 ' "$WORK/key.log")"
    check "deploypro project key prints a public key" test -n "$PUBLIC"
    check "and never the private one" bash -c "! grep -q 'PRIVATE KEY' '$WORK/key.log'"
    # Restricted as GitHub restricts a deploy key: git only, nothing else.
    printf '%s %s\n' \
        'command="git-shell -c \"$SSH_ORIGINAL_COMMAND\"",no-port-forwarding,no-pty,no-agent-forwarding' \
        "$PUBLIC" >"$WORK/sshd/authorized_keys"

    deploy
    wait_for 15 "with the key added, the private repository deploys" \
        serves "$DOMAIN" "version=v8-private"
    check "the deploy log says it used the deploy key" \
        test "$(sql "select count(*) from deployment_logs where line like '%with its deploy key%'")" -ge 1
    check "no log line anywhere contains a private key" \
        test "$(sql "select count(*) from deployment_logs where line like '%PRIVATE KEY%'")" = 0
    check "no decrypted key is left on disk" \
        test "$(ls "$DEPLOYPRO_BUILD_ROOT/ssh")" = known_hosts
    # Looked up with ssh-keygen -F, not grep: ssh may store the host name
    # hashed (HashKnownHosts, the Debian and Ubuntu default).
    check "the git server's identity was remembered" \
        ssh-keygen -F "[127.0.0.1]:$SSH_PORT" -f "$DEPLOYPRO_BUILD_ROOT/ssh/known_hosts"

    # The server's identity changes: what an impostor would look like.
    kill "$SSHD_PID" && wait "$SSHD_PID" 2>/dev/null || true
    rm -f "$WORK/sshd/host_key" "$WORK/sshd/host_key.pub"
    ssh-keygen -q -t ed25519 -N '' -f "$WORK/sshd/host_key"
    start_sshd
    wait_for 10 "the server is back with a different identity" \
        bash -c "echo >/dev/tcp/127.0.0.1/$SSH_PORT"
    check "a server whose identity changed is refused" \
        bash -c "! '$DEPLOYPRO' deploy '$SLUG' >'$WORK/impostor.log' 2>&1"
    check "with ssh's host key warning" grep -qi "host key" "$WORK/impostor.log"
    check "and production was not touched" serves "$DOMAIN" "version=v8-private"
fi

step "the dashboard"
COOKIE="$(curl -s -c - -o /dev/null -X POST -d "token=$DEPLOYPRO_API_TOKEN" http://127.0.0.1:18000/login | awk '/deploypro/ {print $6"="$7}' | tail -1)"
check "the project page shows the volume" \
    bash -c "curl -s -b '$COOKIE' http://127.0.0.1:18000/projects/$SLUG | grep -q deploypro_${SLUG}_recordings"

step "the GitHub App: connect, import a private repository, deploy on push"
DASH=http://127.0.0.1:18000
JAR="$WORK/cookies"
curl -s -c "$JAR" -o /dev/null -X POST -d "token=$DEPLOYPRO_API_TOKEN" "$DASH/login"
check "without the app, New project offers to connect GitHub" \
    bash -c "curl -s -b '$JAR' $DASH/projects/new | grep -q 'Connect GitHub'"
curl -s -b "$JAR" -c "$JAR" "$DASH/github/connect" >"$WORK/connect.html"
STATE="$(awk '$6 == "deploypro_github_state" {print $7}' "$JAR")"
check "the connect page sends a manifest to GitHub with a state" \
    grep -q "127.0.0.1:$GH_PORT/settings/apps/new?state=$STATE" "$WORK/connect.html"
redirect() { curl -s -b "$JAR" -o /dev/null -w '%{redirect_url}' "$DASH$1"; }
check "a code arriving without the right state is refused" \
    bash -c "[[ '$(redirect "/github/created?code=good-code&state=forged")' == */github/connect* ]]"
check "the app is created from GitHub's code, then sent to be installed" \
    test "$(redirect "/github/created?code=good-code&state=$STATE")" = \
    "https://127.0.0.1:$GH_PORT/apps/deploypro-e2e/installations/new"
check "its private key is stored encrypted" \
    test "$(sql "select position('PRIVATE KEY' in convert_from(private_key_encrypted, 'UTF8')) from github_app")" = 0
check "an installation GitHub does not know is refused" \
    bash -c "[[ '$(redirect "/github/installed?installation_id=999&setup_action=install")' == *err=* ]]"
check "the installation is recorded" \
    bash -c "[[ '$(redirect "/github/installed?installation_id=77&setup_action=install")' == *ok=* ]]"
check "New project lists the repository with an Import button" \
    bash -c "curl -s -b '$JAR' $DASH/projects/new | grep -q 'repo=e2e-owner/private-app&installation=77'"
check "the repository really is private" \
    bash -c "! GIT_TERMINAL_PROMPT=0 git ls-remote https://127.0.0.1:$GH_PORT/e2e-owner/private-app.git 2>/dev/null"

gh_status() { sql "select d.status from deployments d join projects p on p.id = d.project_id where p.slug = '$GH_SLUG' order by d.number desc limit 1"; }
gh_live_host() { echo "$(sql "select d.short_id from deployments d join projects p on p.production_deployment_id = d.id where p.slug = '$GH_SLUG'").deploys.test"; }
wait_gh_live() {
    local deadline=$((SECONDS + 150))
    until [ "$(sql "select count(*) from projects p join deployments d on d.id = p.production_deployment_id where p.slug = '$GH_SLUG' and d.git_message is not distinct from $1")" = 1 ]; do
        [ "$(gh_status)" = failed ] && fail "the $GH_SLUG deploy failed: $(sql "select error from deployments d join projects p on p.id = d.project_id where p.slug = '$GH_SLUG' order by d.number desc limit 1")"
        [ "$SECONDS" -ge "$deadline" ] && fail "the $GH_SLUG deploy did not go live in 150s"
        sleep 1
    done
}
IMPORTED="$(curl -s -b "$JAR" -o /dev/null -w '%{redirect_url}' -X POST \
    -d "repo=e2e-owner/private-app&installation=77&name=E2E+GitHub&slug=$GH_SLUG" \
    "$DASH/projects/new/github")"
check "Import makes the project and goes straight to its first deployment" \
    bash -c "[[ '$IMPORTED' == */deployments/$GH_SLUG-* ]]"
wait_gh_live NULL
pass "the private repository built and went live"
wait_for 15 "and serves through the router" serves "$(gh_live_host)" "version="
check "the project is linked to the repository" \
    test "$(sql "select github_repo from projects where slug = '$GH_SLUG'")" = e2e-owner/private-app
check "the log says it cloned through the app" \
    test "$(sql "select count(*) from deployment_logs where line like '%through the GitHub App%'")" -ge 1
check "no log line contains a token" \
    test "$(sql "select count(*) from deployment_logs where line like '%ghs_%' or line like '%x-access-token%'")" = 0
check "the page says pushes deploy it" \
    bash -c "curl -s -b '$JAR' $DASH/projects/$GH_SLUG | grep -q 'through the GitHub App'"

commit_version v9-app
SHA="$(git -C "$WORK/src" rev-parse HEAD)"
BODY="{\"ref\":\"refs/heads/main\",\"after\":\"$SHA\",\"repository\":{\"full_name\":\"e2e-owner/private-app\"},\"head_commit\":{\"id\":\"$SHA\",\"message\":\"v9-app\",\"author\":{\"name\":\"e2e\"}}}"
SIG="$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$(cat "$WORK/gh-secret")" | awk '{print $NF}')"
apphook() {
    curl -s -o "$WORK/apphook.json" -w '%{http_code}' -X POST -H "X-GitHub-Event: $1" \
        -H "X-Hub-Signature-256: sha256=$2" -H "Content-Type: application/json" \
        -d "$3" "$DASH/github/webhook"
}
check "a push signed with another secret is refused" test "$(apphook push deadbeef "$BODY")" = 401
check "a push signed with the app's secret is accepted" test "$(apphook push "$SIG" "$BODY")" = 202
check "and queued a deployment of that project only" grep -q "\"project\":\"$GH_SLUG\",\"deployment\"" "$WORK/apphook.json"
wait_gh_live "'v9-app'"
wait_for 15 "the push went live with no webhook set up on the repository" \
    serves "$(gh_live_host)" "version=v9-app"

UNINSTALL='{"action":"deleted","installation":{"id":77,"account":{"login":"e2e-owner"}}}'
USIG="$(printf '%s' "$UNINSTALL" | openssl dgst -sha256 -hmac "$(cat "$WORK/gh-secret")" | awk '{print $NF}')"
apphook installation "$USIG" "$UNINSTALL" >/dev/null
check "uninstalling the app on GitHub is noticed" \
    test "$(sql "select count(*) from github_installations")" = 0
check "and the project stops treating pushes as its own" \
    test -z "$(sql "select github_installation_id from projects where slug = '$GH_SLUG'")"

step "deleting a project stops everything it ran"
GH_HOST="$(gh_live_host)"
project_containers() { docker ps -aq --filter "label=deploypro.project=$1" | wc -l; }
check "before: the project has containers" test "$(project_containers "$GH_SLUG")" -ge 1
check "before: its deployment answers on its own address" serves "$GH_HOST" "version="
curl -s -b "$JAR" -o /dev/null -X POST "$DASH/projects/$GH_SLUG/delete"
check "the project is gone from the database" \
    test "$(sql "select count(*) from projects where slug = '$GH_SLUG'")" = 0
check "every one of its containers was removed" test "$(project_containers "$GH_SLUG")" = 0
wait_for 15 "its address no longer serves it" lacks "$GH_HOST" "version="
check "the other project was not touched" serves "$DOMAIN" "version="

# The backstop: a container left behind by a project that no longer exists is
# removed by the worker; an identical one on another installation's network is
# not, because another DeployPro on this daemon labels its containers the same.
docker network create deploypro-e2e-other >/dev/null
docker create --name deploypro-e2e-orphan --network "$DEPLOYPRO_NETWORK" \
    -l deploypro.owner=deploypro -l deploypro.project=gone-project "$TRAEFIK_IMAGE" >/dev/null
docker create --name deploypro-e2e-foreign --network deploypro-e2e-other \
    -l deploypro.owner=deploypro -l deploypro.project=gone-project "$TRAEFIK_IMAGE" >/dev/null
restart_worker
wait_for 30 "the worker removes a container whose project is gone" \
    bash -c "! docker inspect deploypro-e2e-orphan >/dev/null 2>&1"
check "but never one on another installation's network" docker inspect deploypro-e2e-foreign
docker rm -f deploypro-e2e-foreign >/dev/null
docker network rm deploypro-e2e-other >/dev/null

printf '\n\033[32mall %s checks passed\033[0m\n' "$PASSED"
