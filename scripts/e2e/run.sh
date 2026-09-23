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
# Everything it creates is namespaced (`forge-e2e` network, `e2e-bv` project,
# its own ports) and removed at the end, so it can run on a host that also
# runs Forge for real. Set KEEP=1 to leave it all running for a look around.
#
#   ./scripts/e2e/run.sh
set -Eeuo pipefail
# Any command failing outside a check still says where, instead of the run
# just stopping: an end-to-end run that can end silently cannot be trusted.
trap 'fail "unexpected error on line $LINENO: $BASH_COMMAND"' ERR

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
VENV="${VENV:-$REPO/.venv}"
FORGE="$VENV/bin/forge"
HTTP_PORT="${E2E_HTTP_PORT:-18080}"
GIT_PORT="${E2E_GIT_PORT:-18443}"
SINK_PORT="${E2E_SINK_PORT:-18090}"
PG_PORT="${E2E_PG_PORT:-55433}"
TRAEFIK_IMAGE="${TRAEFIK_IMAGE:-traefik:v3.7}"
TRAEFIK_RELEASE="${TRAEFIK_RELEASE:-v3.7.13}"
SLUG=e2e-bv
DOMAIN=e2e-bv.test
WORK="$(mktemp -d /var/tmp/forge-e2e.XXXXXX)"
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
draining() { docker ps --format '{{.Names}}' --filter "label=forge.project=$SLUG" | grep -q '\.draining\.'; }
alerted() { grep -q -- "$1" "$WORK/alerts.log" 2>/dev/null; }
web_container() { sql "select container_id from deployments d join projects p on p.production_deployment_id = d.id"; }
forge_images() { docker images -q --filter "label=forge.instance=forge-e2e" | sort -u; }
no_draining() { ! docker ps -a --format '{{.Names}}' --filter "label=forge.project=$SLUG" | grep -q '\.draining\.'; }

# `deploy_expecting_failure` — queue a deploy and wait for it to fail.
deploy_expecting_failure() {
    "$FORGE" deploy "$SLUG" >/dev/null
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
    "$FORGE" deploy "$SLUG" "$@" >/dev/null
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
        git -c user.email=e2e@forge -c user.name=e2e commit -qm "$version"
        git push -q "$WORK/git/app.git" "$branch"
        git checkout -q main
    )
}

restart_worker() {
    [ -n "${WORKER_PID:-}" ] && kill "$WORKER_PID" 2>/dev/null && wait "$WORKER_PID" 2>/dev/null || true
    "$VENV/bin/python" -m forge.worker >>"$WORK/worker.log" 2>&1 &
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
    docker ps -aq --filter "label=forge.project=$SLUG" | xargs -r docker rm -f >/dev/null 2>&1 || true
    docker rm -f forge-e2e-router >/dev/null 2>&1 || true
    docker volume ls -q --filter "label=forge.project=$SLUG" | xargs -r docker volume rm >/dev/null 2>&1 || true
    docker images -q "forge/$SLUG" | sort -u | xargs -r docker rmi -f >/dev/null 2>&1 || true
    docker network rm forge-e2e >/dev/null 2>&1 || true
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
    run_pg "$PG_BIN/initdb" -D "$WORK/pg/data" -A trust -U forge >/dev/null
    run_pg "$PG_BIN/pg_ctl" -D "$WORK/pg/data" -l "$WORK/pg/log" \
        -o "-p $PG_PORT -k $WORK/pg -c listen_addresses=127.0.0.1" start >/dev/null
    psql -h 127.0.0.1 -p "$PG_PORT" -U forge -d postgres -qc "create database forge"
    export DATABASE_URL="postgresql://forge@127.0.0.1:$PG_PORT/forge"
fi

FORGE_MASTER_KEY="$("$FORGE" keygen)"
export FORGE_MASTER_KEY
export FORGE_API_TOKEN=e2e-token
export FORGE_DEPLOY_DOMAIN=deploys.test
export FORGE_CERT_RESOLVER=
export ENVIRONMENT=development
export FORGE_NETWORK=forge-e2e
export FORGE_BUILD_ROOT="$WORK/build"
export FORGE_ROUTER_CONFIG_DIR="$WORK/router"
export FORGE_HEALTH_TIMEOUT=60
export FORGE_ALERT_WEBHOOK_URL="http://127.0.0.1:$SINK_PORT/hook"
export FORGE_MONITOR_INTERVAL=2
export FORGE_KEEP_IMAGES=2
mkdir -p "$FORGE_BUILD_ROOT" "$FORGE_ROUTER_CONFIG_DIR"
"$FORGE" migrate >/dev/null
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
docker network create forge-e2e >/dev/null
docker run -d --name forge-e2e-router --network forge-e2e -p "127.0.0.1:$HTTP_PORT:80" \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v "$FORGE_ROUTER_CONFIG_DIR:/etc/traefik/dynamic:ro" "$TRAEFIK_IMAGE" \
    --providers.docker=true --providers.docker.exposedByDefault=false \
    --providers.docker.network=forge-e2e \
    --providers.file.directory=/etc/traefik/dynamic --providers.file.watch=true \
    --entrypoints.web.address=:80 >/dev/null
wait_for 20 "router is answering" get nothing.deploys.test /
check "router talks to this Docker daemon" \
    bash -c "! docker logs forge-e2e-router 2>&1 | grep -q 'client version .* is too old'"

# The test repository, served over HTTPS because Forge refuses file://.
mkdir -p "$WORK/git"
cp -r "$HERE/app" "$WORK/src"
(cd "$WORK/src" && git init -q -b main . && git add -A &&
    git -c user.email=e2e@forge -c user.name=e2e commit -qm init)
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

restart_worker

# ---------------------------------------------------------------------------

step "first deploy, with a volume"
"$FORGE" project create --name "E2E BalanceVid" --slug "$SLUG" \
    --repo "https://127.0.0.1:$GIT_PORT/app.git" >/dev/null
"$FORGE" volume add "$SLUG" recordings /data >/dev/null
"$FORGE" project set "$SLUG" --stop-timeout 120 >/dev/null
commit_version v1
deploy
V1="$(deployment_host 1)"
wait_for 15 "deployment #1 serves on its own URL through the router" serves "$V1" "version=v1"
check "the app can write to the volume" uploads "$V1" wedding

step "production domain, worker and cron"
"$FORGE" domain add "$SLUG" "$DOMAIN" --primary >/dev/null
sql "update domains set verified_at = now()" >/dev/null # DNS cannot be checked here
"$FORGE" process add "$SLUG" render --type worker --command worker >/dev/null
"$FORGE" process add "$SLUG" tidy --type cron --command job --schedule "* * * * *" \
    --timeout 30 >/dev/null
commit_version v2
deploy
check "the route file has an extension Traefik loads" test -f "$FORGE_ROUTER_CONFIG_DIR/project-$SLUG.yml"
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
"$FORGE" promote "$SLUG" '#1' >/dev/null
check "rollback took under 10s ($((SECONDS - started))s)" test $((SECONDS - started)) -lt 10
wait_for 10 "the production domain serves v1 again" serves "$DOMAIN" "version=v1"
check "every file survived" serves "$DOMAIN" "birthday.mp4,events.log,wedding.mp4"
"$FORGE" promote "$SLUG" '#3' >/dev/null
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
"$FORGE" project set "$SLUG" --stop-timeout 5 >/dev/null
echo 120s | "$FORGE" env set "$SLUG" RENDER_TIME - --target production >/dev/null
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
"$VENV/bin/uvicorn" forge.main:app --host 127.0.0.1 --port 18000 >"$WORK/api.log" 2>&1 &
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
"$FORGE" test-alert >/dev/null
wait_for 10 "a test alert reaches the webhook" alerted "Test alert"

PROD="$(web_container)"
docker stop -t 1 "$PROD" >/dev/null
wait_for 30 "a stopped production site is reported down" alerted "E2E BalanceVid is down"
docker start "$PROD" >/dev/null
wait_for 30 "and reported back when it serves again" alerted "E2E BalanceVid is serving again"
check "a site that is down is reported once, not every check" \
    test "$(grep -c 'E2E BalanceVid is down' "$WORK/alerts.log")" = 1

docker stop -t 1 "forge-$SLUG-render-0" >/dev/null
wait_for 30 "a stopped worker is reported" alerted "worker render is not running"
docker start "forge-$SLUG-render-0" >/dev/null
wait_for 30 "and its recovery" alerted "worker render is running again"

commit_version crash
deploy_expecting_failure
wait_for 10 "a failed production deploy is reported" alerted "failed"
check "and production kept serving the last good version" serves "$DOMAIN" "version=v6-webhook"
check "the stop-timeout kill earlier was reported too" alerted "killed at the end of their stop timeout"

step "cleaning up old images"
before="$(forge_images | wc -l)"
"$FORGE" housekeeping >"$WORK/housekeeping.log"
after="$(forge_images | wc -l)"
check "old images were removed ($before -> $after)" test "$after" -lt "$before"
check "every cleanup step ran without an error" bash -c "! grep -q error '$WORK/housekeeping.log'"
check "including the log and job-run retention queries" grep -q "job runs deleted" "$WORK/housekeeping.log"
check "production's image survived" \
    docker image inspect "$(sql "select image_tag from deployments d join projects p on p.production_deployment_id = d.id")"
check "production still serves" serves "$DOMAIN" "version=v6-webhook"
"$FORGE" promote "$SLUG" '#1' >"$WORK/promote.log" 2>&1 || true
check "rolling back to a removed image says to redeploy instead" \
    grep -q "Redeploy the commit instead" "$WORK/promote.log"

step "backup, and a restore after losing the volume"
"$FORGE" backup --dest "$WORK/backups" >"$WORK/backup.log"
BACKUP="$(ls -d "$WORK"/backups/forge-* | tail -1)"
check "a backup was written" test -f "$BACKUP/manifest.json"
check "it has the database" test -s "$BACKUP/forge.dump"
check "it has the volume, with the recordings in it" \
    bash -c "tar -tzf '$BACKUP/volumes/forge_${SLUG}_recordings.tar.gz' | grep -q 'data/wedding.mp4'"
check "it does not have the master key" bash -c "! grep -rq '$FORGE_MASTER_KEY' '$BACKUP'"

# The disaster: every container of the project and the volume, gone.
docker ps -aq --filter "label=forge.project=$SLUG" | xargs -r docker rm -f >/dev/null
docker volume rm "forge_${SLUG}_recordings" >/dev/null
check "the volume is really gone" bash -c "! docker volume inspect forge_${SLUG}_recordings"
"$FORGE" restore-volume "$BACKUP" "$SLUG" recordings >/dev/null
commit_version v7-restored
deploy
wait_for 20 "after restoring and deploying, the site is back" serves "$DOMAIN" "version=v7-restored"
check "with the first recording" serves "$DOMAIN" "wedding.mp4"
check "and the one rendered during a deploy" serves "$DOMAIN" "birthday.mp4"
check "and the app can still write there (ownership survived)" uploads "$DOMAIN" after-restore

psql "$DATABASE_URL" -qc "create database forge_restored"
RESTORED="${DATABASE_URL%/*}/forge_restored"
pg_restore --no-owner -d "$RESTORED" "$BACKUP/forge.dump"
check "the database dump restores into an empty database" \
    test "$(psql "$RESTORED" -tAc "select count(*) from deployments")" -ge 8
check "with the project, its volume and its encrypted variables" \
    test "$(psql "$RESTORED" -tAc "select count(*) from projects p join volumes v on v.project_id = p.id join env_vars e on e.project_id = p.id")" = 1

step "the dashboard"
COOKIE="$(curl -s -c - -o /dev/null -X POST -d "token=$FORGE_API_TOKEN" http://127.0.0.1:18000/login | awk '/forge/ {print $6"="$7}' | tail -1)"
check "the project page shows the volume" \
    bash -c "curl -s -b '$COOKIE' http://127.0.0.1:18000/projects/$SLUG | grep -q forge_${SLUG}_recordings"

printf '\n\033[32mall %s checks passed\033[0m\n' "$PASSED"
