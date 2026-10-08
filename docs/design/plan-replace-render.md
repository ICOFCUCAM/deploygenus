# DeployPro — Plan: replacing Render for CineForge and Dispatch

**Status:** **PROPOSED 2026-10-08, for the owner to approve phase by phase.** Phase 1 (built-in Redis) is specified in full and is ready to build. The later phases are scoped so they can be estimated, not designed.
**Why now:** all four Render services (`cineforge-redis`, `dispatch-api`, `dispatch-worker`, `cineforge-worker`) show *Suspended by Render*. The owner's decision: *"we need the system working. we can always upgrade our server."*
**Input:** the owner's planning conversation (a Render-replacement architecture with phases: runtime → deployment engine → workers → multi-server). This document keeps that direction and checks it against what DeployPro already is.

---

## 1. Where DeployPro stands today (checked in the code, not assumed)

| # | Capability | Today | Where |
|---|---|---|---|
| 1 | Deploy from Git / GitHub | **Yes** — GitHub App, push-to-deploy, any Git URL with deploy keys | `engine/github.py`, `routers/webhooks.py` |
| 2 | Docker builds | **Yes** — the repo's Dockerfile, or a detected framework | `domain/detect.py`, `engine/pipeline.py` |
| 3 | Image registry | **Not needed on one server** — images are built and kept on the host. Needed only for several servers (Phase 4) | — |
| 4 | Container deployment | **Yes**, zero-downtime handover | `engine/promote.py` |
| 5 | Environment variables / secrets | **Yes**, encrypted with the master key; reach builds only as a secret mount | `engine/environment.py` |
| 6 | HTTPS and domains | **Yes** — Traefik, a wildcard certificate, custom domains, Cloudflare DNS made automatically | `engine/routing.py`, `engine/dns.py` |
| 7 | Health checks | **Yes** — before promotion, and a monitor every minute | `engine/health.py`, `engine/monitor.py` |
| 8 | Restart policy | **Yes** — `unless-stopped` on every container | `adapters/containers.py` |
| 9 | Rollback | **Yes** — instant for kept-warm deployments, otherwise from the image | `engine/promote.py` |
| 10 | Logs | **Yes** — build and runtime logs, live in the dashboard | `engine/logs.py` |
| 11 | CPU / RAM limits | **Yes** — per project, per worker | `Project.cpu_shares`, `memory_mb` |
| 12 | Several servers | **No** — one DeployPro manages one host | Phase 4 |
| 13 | Workers and scheduled jobs | **Yes** — long-running workers with replicas; cron jobs | `engine/processes.py`, `engine/scheduler.py` |
| 14 | Redis | **No** | **Phase 1, this document** |
| 15 | GPU servers | **No** | Phase 5 |
| 16 | RunPod | **No integration** — apps call RunPod themselves with an API key in their environment, which works today | Phase 5 |
| 17 | API and webhooks | **Yes** — REST API with a token (`/api/projects`, `/api/deployments`, promote, redeploy, cancel, logs) and the `deploypro` command | `routers/` |
| 18 | Staging vs production | **Partly** — every branch can deploy as a preview with its own address and preview-only variables. No separate long-lived "staging" environment | later, if needed |
| 19 | Autoscaling | **No** — replicas are set by hand. Not needed at this size | not planned |
| 20 | Monitoring | **Partly** — uptime checks, disk, backups and Discord/Slack alerts. No metrics history (CPU graphs, response times) | later, if needed |

**Conclusion:** 13 of the 20 are done. What stands between Render and DeployPro for these four services is **Redis (14)**, **capacity on the server (12, partly)**, and two gaps the repositories themselves revealed: **G1 Dockerfile path** and **G2 background services** (§2a) — both now built. Everything else on the list is either working or not needed to get the system running.

**Deliberately not in this plan:** Kubernetes, Loki/Grafana, Prometheus, a container registry and autoscaling. Each solves a problem these apps don't have yet, and each would cost weeks. They return to the table only when a phase below actually needs them.

## 2. Render → DeployPro, service by service

| Render service | Runtime | DeployPro equivalent | Ready? |
|---|---|---|---|
| `dispatch-api` | Docker, web, Frankfurt | A **project** from its repository, with **Dockerfile path** `services/dispatch-api/Dockerfile` (G1) | **Yes, with G1** |
| `dispatch-worker` | Docker, background, **own Dockerfile** | A **background service** project (G2, §2a) | **Ready** (G2 built) |
| `cineforge-worker` | Docker, background, **own Dockerfile** | A **background service** project (G2, §2a) | **After Redis** (G2 built) |
| `cineforge-redis` | Valkey 8 | **Built-in Redis** on the cineforge project (Phase 1) | **After Phase 1** |
| Postgres (Supabase) | managed | **Stays on Supabase**; its URL goes into Environment as now | **Yes** |
| GPU work (RunPod L4 pod) | GPU | **Stays on RunPod**; workers keep calling it with their API key | **Yes** |

Only CineForge uses Redis (`cineforge-redis`, read as `REDIS_URL`); Dispatch uses none (§2a).

## 2a. What the two repositories showed (read 2026-10-08)

| | Dispatch — `ICOFCUCAM/SOVEREIGN` | CineForge — `ICOFCUCAM/media` |
|---|---|---|
| Render setup | `render.yaml`: `dispatch-api` (web, health `/v1/health`, port 8787) and `dispatch-worker` (background) | `render.yaml`: `cineforge-worker` (background) and `cineforge-redis` (Key Value, `noeviction`, internal only). The web app is on Vercel |
| Dockerfiles | `services/dispatch-api/Dockerfile`, `services/dispatch-worker/Dockerfile` (its own image, with Chromium for PDFs), both built **from the repository root** | `apps/worker/Dockerfile`, built **from the repository root** |
| Redis | **none** — Postgres (Supabase) and S3 (Supabase Storage) only | **`REDIS_URL`**, used by BullMQ (`packages/gpu`, `packages/realtime`) |
| GPU | — | RunPod, fal.ai and others through their API keys (unchanged) |

Two gaps follow that Redis alone does not close:
- **G1 · Dockerfile path.** DeployPro built `<root directory>/Dockerfile` from that folder. These repositories need a Dockerfile in a subfolder **built from the repository root** (Render's `dockerfilePath` + `dockerContext: .`). **Built 2026-10-08:** the project setting **Dockerfile path** (Build page, API, `deploypro project set --dockerfile`), migration `0011_dockerfile_path.sql`. Checked by building SOVEREIGN's `services/dispatch-api/Dockerfile` from its root: its `COPY packages/…` lines resolve.
- **G2 · Background services.** Every DeployPro project has a web container that must answer HTTP. `dispatch-worker` and `cineforge-worker` have **no web part and their own Dockerfiles**, so they can be neither a project nor a worker of another project today. Needed: a project kind **Background service** — built and deployed as now, but with no address, no router, no HTTP health check (healthy = still running after a settle time), and rollback as usual. Its environment, volumes, build arguments and Redis work as for any project. **Built 2026-10-08:** the switch **Background service (no website)** on the Build page and when importing or creating a project; migration `0012_project_kind.sql`. Such a project starts from the image's own `CMD` with no router labels and no port; it passes if it is still running **and has not restarted** 15 s after starting (the restart policy brings a crashing container straight back, so "running" alone misses a crash loop — found with real Docker), otherwise the deploy fails with the container's last 100 lines. Pushes to other branches are ignored (no previews), no domains can be added, nothing is kept warm (two copies would both consume the queue), and the monitor alerts when its container is not running. Promoting a new deployment overlaps old and new for the settle time, which queue consumers (BullMQ, Postgres queues) tolerate.

**Revised order:** G1 (done) → G2 (done) → **Dispatch moves** (it needs no Redis) → **Redis (Phase 1)** → **CineForge moves**.

## 3. Phase 1 — Built-in Redis

### 3.1 What the owner sees

Configuration gets a **Redis** page (beside Storage):
- **Off:** one sentence about what it is and an **Add Redis** button with a memory size (default 256 MB) and an eviction policy (default *Never evict — refuse writes when full*, which job queues need).
- **On:** state (running, memory used of its limit, keys), the variable name the app receives, **Restart**, **Change memory**, and **Remove Redis** behind a confirmation that says the data is deleted.
- The project **Overview** lists Redis under Runtime, beside workers.

### 3.2 What runs

- **Image:** `valkey/valkey:8-alpine`, the same engine as Render's Redis, BSD-licensed. Pinned by the setting `DEPLOYPRO_REDIS_IMAGE` so a host can choose.
- **One container per project:** `deploypro-<slug>-redis`, on DeployPro's network, **no router labels and no published port**, so it is reachable only by containers on that network and never from the internet.
- **Started with:** `--requirepass <secret>`, `--maxmemory <90% of the limit>`, `--maxmemory-policy <policy>`, `--appendonly yes`, `--appendfsync everysec`, `--dir /data`. The container is capped at the chosen memory, and `--restart unless-stopped`.
- **Data:** a Docker volume `deploypro_<slug>_redis` at `/data`, owned by DeployPro and **not** mounted into the app.

### 3.3 How the app finds it

- **`REDIS_URL=redis://default:<password>@deploypro-<slug>-redis:6379/0`** is added to the runtime environment of the project's **production** web container, workers and jobs. It is the name Render uses, so most apps need no change.
- **The name is configurable** per project (for example `VALKEY_URL`, or a second name such as `CELERY_BROKER_URL`), set on the Redis page.
- **Production only, like volumes.** Previews don't get it, so a preview build can't consume production's queue. A preview that needs Redis gets its own variable by hand.
- A variable the owner has set with the same name under Environment **wins**, and the page says so. That keeps pointing at an outside Redis possible.

### 3.4 Lifecycle

- **Not tied to deploys.** A deploy, promotion or rollback never restarts Redis. Queued jobs survive a deploy, which is the point of having it.
- **Kept running:** the worker's existing reconcile pass (`engine/processes.py`) also ensures the Redis container exists and runs the configured image and memory. A stopped one is started; a changed setting recreates it on the same volume.
- **Restart and memory changes** recreate the container. The data persists through the volume.
- **Remove:** stops and deletes the container. The volume is deleted only when the confirmation is ticked; otherwise it is kept and shown as "kept, not running".
- **Project deletion** (`engine/service.delete_project`) removes the Redis container and volume with the rest.

### 3.5 Backups and monitoring

- **Backups:** `engine/backup.py` exports the project's volumes. The Redis volume is added explicitly, not registered as a project `Volume`, because that would mount it into the app. Before the export, DeployPro runs `valkey-cli BGREWRITEAOF` and waits for it, so the copy is compact and consistent. Valkey loads a truncated last write anyway (`aof-load-truncated yes`).
  - The off-server copy to the Storage Box includes it automatically.
  - `deploypro restore-volume` restores it like any other volume.
- **Monitoring:** the monitor already checks every minute. It also runs `valkey-cli ping` in the container and raises the normal *down / recovered* alert. Memory used above 90% of the limit raises a warning, because with *noeviction* a full Redis refuses new jobs.

### 3.6 Data model and code

| Layer | Change |
|---|---|
| `db/migrations/0013_redis.sql` | Table `project_redis`: `project_id` (PK, FK), `memory_mb` (default 256), `policy` (`noeviction` default; `allkeys-lru`, `volatile-lru`), `env_names` (text, default `REDIS_URL`), `password_encrypted` (bytea), `image`, `created_at`, `updated_at` |
| `domain/redis.py` | Policy names, memory bounds (64–4096 MB), env-name validation, the URL format, the `valkey-server` arguments as pure functions |
| `repositories/redis.py` | get / enable / update / disable |
| `adapters/containers.py` | `run_service()` for a long-running, unrouted container, plus `exec_capture()` for `valkey-cli` |
| `engine/redis.py` | enable, ensure-running, restart, resize, remove, `url_for(project)`, health ping, pre-backup rewrite |
| `engine/environment.py` / `processes.py` / `launch.py` | Add the Redis variables to the production runtime environment |
| `engine/backup.py`, `engine/monitor.py`, `engine/service.py` | Backup, health and deletion, as above |
| `web/` | The Redis page under Configuration, its routes, and the Overview Runtime line |
| `cli.py` | `deploypro redis enable/status/restart/resize/disable <project>` |
| `docs/design/proposal-redis.md` | The decisions in §3.8, recorded as the other proposals are |

### 3.7 Sharing one Redis between two projects

On Render, Dispatch and CineForge share `cineforge-redis`. Two ways on DeployPro, from simplest:
1. **Copy the URL.** The Redis page shows its full `REDIS_URL` once, behind a click, so the owner can paste it into the other project's Environment. This works because both projects are on the same Docker network. **This is the Phase 1 answer.**
2. **A "use Redis from project …" choice** that injects the other project's URL automatically and keeps it current. Worth adding only if the URL ever has to change, for example on a password rotation.

### 3.8 Decisions (recommended answers, for the owner to confirm)

- **R1 · Engine:** Valkey 8. It is what Render ran, it is open-source, and it speaks the Redis protocol, so BullMQ, Celery, RQ, Sidekiq and ioredis all work unchanged.
- **R2 · Default policy:** `noeviction`. BullMQ requires it, and silently dropping queued jobs is worse than refusing new ones with an error.
- **R3 · Persistence:** AOF every second. At most one second of writes is lost on a crash; cost is negligible at this size.
- **R4 · Previews:** no Redis. The same rule as volumes.
- **R5 · One per project, no replicas:** like Render's starter plans. High availability is Phase 4 territory.

### 3.9 Acceptance — Phase 1 is done when

- On a real Docker host (as the backup copy was tested), **Add Redis** starts Valkey, and an app container on the network can `SET`/`GET` through `REDIS_URL`.
- A deploy, a promotion and a rollback each leave the Redis container untouched, with its keys intact.
- Killing the container: the monitor alerts, reconcile restarts it, and the keys come back from the AOF.
- A backup contains the Redis volume; `restore-volume` on an emptied volume brings the keys back.
- With `noeviction` and a full Redis, a write fails with Redis's `OOM` error rather than data disappearing, and the page warns at 90%.
- No port is published on the host (checked with `docker port`), and the password never appears in a log, an error message or the page apart from the explicit "show URL".
- `scripts/check.sh` passes with tests for each module above.

**Estimate:** about two working days including the real-Docker checks.

## 4. Phase 2 — Move Dispatch and CineForge off Render

Runbook, per application:
1. **Collect from Render** before anything else: each service's environment variables (Render → service → *Environment*, including environment groups), start commands, and health-check path.
   - These are secrets: they go straight into DeployPro's Environment page, never into a chat or a document.
2. **Create the project** in DeployPro from its GitHub repository (**New project → Import**). Set:
   - production branch
   - memory and CPU (Configuration → Build)
   - **Instant rollback: keep 1** to save memory
3. **Add Redis** (Phase 1) on cineforge. Paste its URL into dispatch's Environment (§3.7).
4. **Environment:** the Render variables, minus anything Render-specific, such as `RENDER_*` and Render's Redis URL.
5. **Workers:** Runtime → Workers → `dispatch-worker` and `cineforge-worker`, with the start commands Render used.
6. **Deploy, then verify:**
   - the API answers on its DeployPro address
   - a job sent through the API is picked up by the worker
   - RunPod calls succeed
7. **Domain:** add the API's real domain under Domains. If it is on Cloudflare, its DNS record is made automatically.
8. **Then delete the Render services,** so nothing keeps billing or running twice.

**Settled 2026-10-08:** Dispatch is `ICOFCUCAM/SOVEREIGN` (no Redis); CineForge is `ICOFCUCAM/media` (`REDIS_URL`, BullMQ). Decisions R1–R5 confirmed by the owner. Render services stay until the DeployPro versions have run the real workloads.

## 5. Phase 3 — Capacity: the server, before anything else moves

**The current host can't take these services.** On 2026-10-07 it showed:
- 2 vCPU with a load average of 4.7
- swap at 55%
- BalanceVid's playout engine at 282% of real time
- a second app (`powered-website-visibility-platform`) with five workers sharing the same box

The owner's decision is to upgrade the server as needed. In order:
1. **Free what's free:**
   - pause workers that don't need to run (the visibility platform's `crawl`/`analysis`)
   - BalanceVid's `STREAM_LADDER=off` (done), and `STREAM_QUALITY=low` if needed
   - keep 1 warm deployment per project
2. **Rescale in place** to a 4 vCPU / 8 GB type when one is available in Helsinki: **CX33** (cost-optimised), or **CPX31** if the budget allows. *CPU and RAM only* keeps the downgrade path.
3. **If Helsinki has nothing suitable:** a second server, with its own DeployPro install and its own dashboard, until Phase 4. CineForge and Dispatch would go there; BalanceVid stays where it is.

**Rule for every move:** check `uptime`, `free -h` and BalanceVid's engine load before and after. The TV channel is the most demanding thing on the box and the first to show trouble.

## 6. Later phases (scoped, not designed)

- **Phase 4 — Several servers under one DeployPro.** Needs a registry (or image transfer), a per-server agent, placement rules (which project runs where) and cross-server networking for Redis. A substantial project. **Start it only when a second server actually exists and two dashboards become a real nuisance.**
- **Phase 5 — GPU and RunPod.** First step, cheap: a RunPod connection in DeployPro (API key stored like any secret), so the dashboard can show and start/stop pods and their cost. Later: a job API that sends "standard" work to a Hetzner GPU and "cinematic" work to RunPod. **Only after CineForge runs reliably on Phases 1–3.**
- **Staging, metrics history, autoscaling:** when a real need appears. Each is noted in §1 with what exists today.

## 7. Order of work

| Step | Who | Unblocks |
|---|---|---|
| 1. Free capacity on the current server (§5.1) | owner, 15 min | Stable BalanceVid TV now |
| 2. Confirm R1–R5 (§3.8) | owner — **done** | Phase 1 build |
| 3. G1 Dockerfile path (§2a) | Claude — **done** | All three services build |
| 4. G2 Background services (§2a) | Claude — **done** | Both workers |
| 5. Rescale or add a server (§5.2–5.3) | owner | Room for the migrated services |
| 6. Migrate Dispatch (§4) | together | First service off Render |
| 7. Build built-in Redis (§3) | Claude, ~2 days | CineForge's queue |
| 8. Migrate CineForge (§4) | together | Render no longer needed |
| 9. Delete the Render services, after the real workloads ran | owner | No double running, no Render bill |
| 10. Phases 4–5 | later | Multi-server, GPU orchestration |
