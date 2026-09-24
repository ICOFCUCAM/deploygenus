# DeployPro

A self-hosted deployment platform. Push to a branch; a container is built,
started, health-checked and routed, on hardware you own.

```
git push  →  clone  →  detect  →  docker build  →  run  →  health check  →  route
                                                                              │
                         every deployment keeps its own permanent URL  ───────┤
                         production is a pointer you can move in a second ────┘
```

It is built for the case Vercel is bad at and charges for: long-running
servers, background processes, APIs, and sites whose bill should be a fixed
monthly number rather than a function of traffic.

## Status

**DeployPro has been run end to end on a real Docker daemon, a real Traefik and a
real Postgres.** `scripts/e2e/run.sh` deploys a test app from a git push, then:
- routes a production domain
- deploys again during a render and checks the render finishes
- rolls back
- checks a preview cannot see production's files
- runs a cron job against the shared volume
- checks a stop timeout is enforced
- deploys from a signed webhook
- sends alerts for a site down, a worker stopped and a failed deploy, and
  for each recovery
- cleans up old images without touching production
- restores a deleted volume from a backup, and restores the database dump
- deploys a private repository over SSH with its deploy key, and refuses a git
  server whose identity has changed
- connects a GitHub App through the manifest flow, imports a private
  repository from the list, deploys it with a token for that repository only,
  and deploys again from a signed push, against a stand-in for GitHub that
  checks the app's JWT and tokens the way GitHub does
- deletes a project and checks every one of its containers is gone and its
  address stops answering, and that the worker removes containers left by a
  deleted project without touching another installation's

It makes 113 checks in about four minutes. The first 79 passed ten runs in a
row. Its runs have found five bugs that would have hit real installations.
All five are fixed.

482 unit tests cover everything that does not need a daemon. What is still
unproven, chiefly HTTPS and DeployPro's own container image, is listed under
[What is proven and what is not](#what-is-proven-and-what-is-not).

The dashboard is server-rendered from the control plane itself — no build
step, no separate deployment, and every action on it is a plain form that
works without JavaScript. That is deliberate: it is the page you open when a
deploy has gone wrong, so it must not depend on anything that could be wrong
at the same time. Its design (information architecture, states, the visual
system and page layouts) is specified in [`docs/design/`](docs/design/), and
the pages implement those documents.

## What it does

| | |
| --- | --- |
| **Deploys anything containerisable** | Next.js, Astro, Nuxt, SvelteKit, Remix, Vite, Django, FastAPI, Flask, Go, static — or your own `Dockerfile`, which is always honoured |
| **A permanent URL per deployment** | `blog-3f9a2c71.deploys.example.com` keeps working after twenty more deploys |
| **Promotion is a pointer** | Production is a file the router watches. Moving it touches no container |
| **Rollback in about a second** | The same operation as promotion, run against an older deployment |
| **Encrypted environment variables** | Fernet at rest, scoped to production or preview, never readable back through the API |
| **Custom domains with automatic TLS** | With a canonical redirect from every alias to the primary |
| **Preview deploys** | Any non-production branch gets a URL and is kept away from production secrets |
| **A dashboard** | Projects, deployments with a live build log and lifecycle, runtime, configuration and one-click rollback |
| **Background workers** | Long-running processes from the same image as the site — queue consumers, listeners |
| **Scheduled jobs** | Five-field cron in UTC, with run history, captured output and catch-up after downtime |
| **Persistent volumes** | Storage that survives every deploy, shared by production, its workers and its jobs |
| **Graceful replacement** | A replaced container gets a per-project stop timeout to finish its work, without holding up the deploy |
| **Alerts** | Slack or Discord, once when something breaks and once when it recovers: failed deploys, sites down, workers stopped, failing jobs, a full disk |
| **Daily backups** | The database and every volume, rotated, checksummed, and tested by restoring them |
| **Cleans up after itself** | Old images, build cache and logs are cleared hourly, so the disk does not fill |
| **Fast rebuilds** | Package downloads are cached between builds (npm, pnpm, yarn, bun, pip, Go) |

## How it works

Four processes and the containers they create.

```
                    :80 :443
                       │
                ┌──────▼───────┐        reads container labels
                │   Traefik    │◄───────────────────────────────┐
                │  (the edge)  │                                │
                └──┬────────┬──┘                                │
       watches ────┘        └──── routes to ──┐                 │
            │                                 │                 │
   ┌────────▼─────────┐          ┌────────────▼──────────────┐  │
   │ router config    │          │  blog-3f9a2c71  (live)    │──┘
   │ project-blog.yml │          │  blog-9d1e0f44  (warm)    │
   └────────▲─────────┘          │  api-77c2b310   (live)    │
            │ writes             └───────────────────────────┘
            │                                 ▲
   ┌────────┴──────┐   claims    ┌────────────┴──────┐
   │  control API  │◄────────────┤   deploy worker   │ builds & runs
   └───────┬───────┘  deployments└─────────┬─────────┘
           │                               │
           └────────► Postgres ◄────────────┘
```

**The API** takes requests and queues work. **The worker** claims one
deployment at a time (`FOR UPDATE SKIP LOCKED`, so a second worker is safe to
add) and runs it to completion. **Postgres** is the queue — there is no broker,
because the state is already in a transactional store and a second system
holding "which deployment is building" is a second thing to disagree.

**Traefik** routes by two independent mechanisms, and the split matters:

- A deployment's own permanent hostname is a **container label**, set once at
  `docker run` and never touched again.
- Production domains are a **file** the router watches.

Docker cannot change a running container's labels. If production lived in
labels, promoting would mean destroying and recreating the container — a
restart, a cold cache and a few seconds of 502 on every promotion and every
rollback. Writing a small JSON file instead makes promotion atomic and
instant, and makes rollback the identical operation.

### The deployment lifecycle

```
queued ──► building ──► deploying ──► ready
   └───────────┴────────────┴───────► failed
```

Production moves **last**, only after the new container has answered a real
HTTP request. A failed deploy cannot take a site down, because nothing was
ever pointed at it.

A deployment that succeeds is immutable. Superseded ones keep their images and
have their containers reclaimed after `keep_warm`, so rolling back is a
restart rather than a rebuild.

## Setting it up

**On a fresh Ubuntu or Debian server, one command does all of it:**

```bash
git clone https://github.com/ICOFCUCAM/deploygenus.git /opt/deploypro && cd /opt/deploypro
DEPLOYPRO_DEPLOY_DOMAIN=deploys.example.com DEPLOYPRO_ACME_EMAIL=you@example.com \
CF_DNS_API_TOKEN=… bash scripts/install.sh
```

It installs Docker and generates the secrets. It checks your DNS and opens the
firewall. It starts the stack, migrates and runs `deploypro doctor`. It also
installs a `deploypro` command on the server. Run it again to upgrade.
[docs/02-hetzner-cloudflare.md](docs/02-hetzner-cloudflare.md) walks through
the whole thing on Hetzner with Cloudflare DNS, from creating the server to a
live site.

The manual steps, if you would rather see each one:

You need a host with Docker, a domain, and a DNS provider with an API token.

**1. DNS.** Point a wildcard at the host:

```
*.deploys.example.com.   A   203.0.113.10
 deploys.example.com.    A   203.0.113.10
```

**2. Configure.**

```bash
cp .env.example .env
```

Fill in `DEPLOYPRO_DEPLOY_DOMAIN`, `DEPLOYPRO_ACME_EMAIL`, `DEPLOYPRO_DNS_PROVIDER` and
its API token, then generate the three secrets:

```bash
openssl rand -hex 32                                 # DEPLOYPRO_API_TOKEN
openssl rand -hex 24                                 # POSTGRES_PASSWORD
docker compose run --rm --no-deps api deploypro keygen   # DEPLOYPRO_MASTER_KEY
```

Keep a backup of `DEPLOYPRO_MASTER_KEY`. Losing it makes every stored environment
variable unreadable, and changing it has exactly the same effect.

**3. Start, and create the schema.**

```bash
docker compose up -d
docker compose exec api deploypro migrate
docker compose exec api deploypro doctor
```

`doctor` checks the things that are actually wrong when nothing deploys: the
Docker socket, the writable volumes, whether the wildcard resolves, and
whether TLS is configured at all.

### Why a DNS API token is required

Every deployment invents a new hostname. If each one asked for its own
certificate, Let's Encrypt's limit of **50 new certificates per registered
domain per week** would be spent by about seven deploys a day — and then
nothing would get a certificate, including the custom domains carrying real
traffic.

So DeployPro issues **one wildcard certificate** for `*.deploys.example.com`, and
every deployment router inherits it. A wildcard can only be proven over a
DNS-01 challenge, which is why a DNS provider token is not optional. Customer
domains are separate and low-volume: they get individual certificates over
HTTP-01.

## Your first deploy

The quickest way is the dashboard: **New project → Connect GitHub**, once,
then **Import** next to any repository (see [The GitHub App](#the-github-app)).
From the command line:

```bash
deploypro project create --name "Blog" --repo https://github.com/you/blog.git
deploypro deploy blog
deploypro logs blog-3f9a2c71 --follow
```

```
·· deploying Blog #1 — main at 4f2a9c1e (manual)
·· cloning https://github.com/you/blog.git at 4f2a9c1e
·· Next.js with output: 'standalone' — serving the traced server bundle
·· building image deploypro/blog:4f2a9c1e88b1
   #8 [build 4/4] RUN npm run build
   …
·· image built
·· starting container on port 8080 with 3 environment variables
·· healthy after 1.4s (4 attempts) — answered 200 on /
·· ready at https://blog-3f9a2c71.deploys.example.com
·· no verified custom domains — serving on https://blog-3f9a2c71.deploys.example.com
```

Then deploy on every push:

```bash
deploypro webhook blog      # prints the payload URL and the secret
```

The same thing is on the project's **Configuration → Repository** page in the dashboard, at
`https://deploypro.deploys.example.com` — sign in with `DEPLOYPRO_API_TOKEN` and the
browser holds a signed cookie derived from it, so there is still only one
credential to keep.

Paste both into the repository's **Settings → Webhooks**. Pushes to the
production branch deploy and promote; pushes to any other branch get a preview
URL and cannot see production-scoped variables.

### The GitHub App

Connect GitHub once and it works the way Vercel's import screen does: your
repositories are listed with an **Import** button, every push deploys by
itself, and private repositories need nothing more. No deploy keys, and no
webhook to add to each repository.

1. In the dashboard, **New project → Connect GitHub → Create the app on
   GitHub**. DeployPro sends GitHub a description of the app it needs (a
   "manifest"); GitHub creates it under your account and hands its private
   key and webhook secret straight back to this server. They exist nowhere
   else, and are stored encrypted with the master key.
2. GitHub asks where to install it. Choose your account and **All
   repositories**, or only the ones to deploy. You can change this later with
   **Choose repositories on GitHub** on the New project page.
3. Back on New project, press **Import** next to a repository. Name, branch
   and root directory are filled in; **Deploy** builds it straight away.

What it may do, and what it may not:

- **It only reads.** The app asks for repository contents and metadata, read
  only, and is sent push events. It cannot write to any repository.
- **It is private.** Only the account that made it can install it, so nobody
  else can point it at their code and have it built on your server.
- **Clones use a token for one repository.** Each clone gets an hour-long
  token that can read that one repository and nothing else. It is passed to git
  as a header through the environment, so it is never in the repository URL,
  never on a command line, never in the checkout's git config and never in a
  log.
- **Pushes are verified.** The app's webhook is signed with its own secret. A
  push is matched to projects by the repository's `owner/name`. Pushes to the
  production branch go live, and pushes to other branches deploy as previews.

A project created from a GitHub URL (in the dashboard or with `deploypro
project create`) is linked to the app automatically when the app can read the
repository. An older project gets a **Link to GitHub** button on its page, or:

```bash
deploypro github                  # the app, where it is installed, linked projects
deploypro github link balancevid  # have the app read and deploy this project
deploypro github unlink balancevid
```

For an organisation's repositories, make the app in the organisation: on the
Connect GitHub page, enter the organisation's name first.

### Private repositories without the GitHub App

For a git host other than GitHub, or if you would rather not use the app, use
the repository's SSH URL and a **deploy key**, a key DeployPro makes for that
one project:

```bash
deploypro project create --name BalanceVid --repo git@github.com:you/balancevid.git
deploypro project key balancevid      # prints the public key
```

On GitHub, open the repository, then **Settings → Deploy keys → Add deploy
key**. Paste the key and leave **Allow write access off**. The project's
**Configuration → Repository** page shows the same key, with a button to make or replace it.

- **It reads one repository, and nothing else.** If it leaked, it could not
  push, and it could not reach your other repositories. It never expires and
  is not tied to your GitHub account.
- **The private half is encrypted** with the master key, like your variables.
  For each git command it is decrypted into a private (0600) file, and deleted
  when the command ends, whether it succeeds or fails. It is never in a log,
  on a command line or in the dashboard.
- **GitHub's server keys are pinned.** A clone from github.com fails rather
  than trusting anyone claiming to be GitHub. Other git hosts are trusted on
  first contact and remembered, so a key that changes later is refused.
- **Tokens in URLs are refused.** `https://TOKEN@github.com/…` would be stored
  in plain text and printed in every deploy log, so DeployPro rejects it and
  points at the deploy key. Any credential that still turns up in git's own
  output is blanked out before it is logged.

`deploypro project key balancevid --rotate` replaces the key. The old one
stops working at once, so swap it on GitHub before the next deploy.

### Environment variables

```bash
deploypro env set blog DATABASE_URL 'postgres://…' --target production
deploypro env set blog STRIPE_KEY - < key.txt        # `-` reads stdin, staying
                                                  # out of your shell history
```

They are applied at **build** time as well as run time, so changing one takes
effect on the next build:

```bash
deploypro deploy blog
```

### Custom domains

```bash
deploypro domain add blog example.com --primary
deploypro domain add blog www.example.com
# point DNS at deploys.example.com, then:
deploypro domain verify blog example.com
```

Verification is a DNS check before the hostname reaches the router — an
unverified name would fail its ACME challenge against a rate limit shared by
every site on the host. Once verified, aliases 301 to the primary, because two
hostnames serving identical pages is a duplicate-content problem.

### Workers and scheduled jobs

This is the part Vercel has no answer for. A process is another container from
the **same image** as the deployment, so the worker consuming your queue is
running exactly the code the website is running — not a second build of the
same commit.

```bash
deploypro process add blog mailer  --type worker --command "node worker.js" --replicas 2
deploypro process add blog nightly --type cron   --command "node cleanup.js" \
                               --schedule "0 3 * * *"

deploypro process list blog
deploypro runs blog nightly --output      # history, and the last run's output
deploypro process run blog nightly        # trigger it now, outside the schedule
```

**Both run against production only.** A preview of a branch must not start a
second consumer on the same queue, and must not run the nightly billing job
against real data because someone opened a pull request.

Workers are recreated on every promotion, so they roll forward with production
**and back with it**. A rollback that left the old workers running the new code
would undo half the change, which is worse than either version on its own.

Scheduled jobs claim the slot their expression names, rather than firing on a
timer. A worker that was down at 03:00 runs the job late instead of skipping
the day, and because the slot is a row with a unique constraint, three workers
sweeping in the same second produce exactly one run. A job still going when its
timeout expires is killed and recorded as `timed_out` — otherwise one hung run
holds the slot and every later run is silently skipped.

Job output is captured and kept with the run, tail-first — the traceback is at
the end, and the amount kept is bounded so a job printing a megabyte a second
cannot fill the database. Workers stream to the container log instead, because
an always-on process would otherwise write an unbounded log table.

### Volumes

Everything a container writes disappears with the container, which happens on
every deploy. That is right for a website and wrong for an app that keeps what
it makes, such as recordings that a worker renders later. A volume is storage
owned by the **project** rather than by a deployment:

```bash
deploypro volume add balancevid recordings /data
deploypro volume list balancevid
deploypro volume rm balancevid recordings    # stops mounting it; the data is kept
```

- It is mounted into the production web container, **every worker and every
  scheduled job**, all at the same path. That is what lets the web app accept
  an upload that a worker then processes.
- It is **never mounted into a preview.** A branch deploy writing into
  production's files is the same mistake as a preview holding the production
  database password.
- It takes effect from the next deploy, because Docker cannot add a mount to a
  running container.
- The Docker volume is called `deploypro_<project>_<name>`, created on first use and
  **never deleted by DeployPro**. Removing a volume, or the whole project, only stops
  mounting it. `deploypro doctor` lists volumes nothing mounts any more; deleting one
  is a deliberate `docker volume rm`.

**The one thing your image must do:** create the mount path and give it to the
user the app runs as. A new volume copies the ownership of the directory it is
mounted over. If the directory is missing, the volume is owned by root, and an
app running as a normal user (as every image DeployPro generates does) cannot write
to it:

```dockerfile
RUN mkdir -p /data && chown node:node /data     # before USER node
```

Back volumes up like a database. DeployPro does not snapshot them.

### Letting work finish: the stop timeout

```bash
deploypro project set balancevid --stop-timeout 1800    # seconds; default 10
```

When a deploy replaces a container, DeployPro does not wait for the old one to
stop. It renames the old container out of the way, sends it its stop signal,
and starts the replacement immediately. The old container keeps running until
it exits or until its stop timeout runs out, and then the worker removes it.
The worker checks every 5 seconds, so a deadline is enforced within 5 seconds
of the timeout.
A render that was halfway through when you pushed still finishes, and the
deploy is not held up while it does.

This applies to workers replaced on a promotion, workers removed or scaled
down, and superseded web containers reclaimed after `keep_warm`. The timeout is
also set on the container itself, so a host shutdown or daemon restart gives
the app the same time.

For this to help, the app has to treat the stop signal (`SIGTERM` unless the
image sets `STOPSIGNAL`) as "take no new work, finish what you have, then
exit". A queue consumer should do that anyway. Old and new workers overlap
while this happens, so the queue must be safe to consume from twice. A
database queue claimed with `FOR UPDATE SKIP LOCKED` is.

### Alerts

```bash
# .env
DEPLOYPRO_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/…   # or a Discord webhook

deploypro test-alert        # sends one, to prove it arrives
```

DeployPro sends a message when:

- **a deploy of the production branch fails.** Production is untouched, and the
  message says so. Failed previews are not sent; whoever pushed them is usually
  watching.
- **a production site stops answering HTTP.** It is checked every minute and
  reported after two failures in a row, so a restart in progress is not an
  outage. You get one message when it goes down and one when it comes back.
- **a worker stops,** because a render worker that dies is invisible from the
  website.
- **a scheduled job starts failing,** and again when it succeeds.
- **a replaced worker is killed at its stop timeout,** which means the timeout
  is too short for its work.
- **the disk passes `DEPLOYPRO_DISK_ALERT_PERCENT`** (90% by default).
- **a backup fails.**

Each ongoing condition is sent once when it starts and once when it ends,
never once a minute.

### Backups

```bash
# .env: daily at 03:00 UTC, newest 7 kept
DEPLOYPRO_BACKUP_DIR=/var/backups/deploypro

deploypro backup             # take one now
deploypro doctor             # shows how old the latest one is
```

Each backup is a directory, `deploypro-<time>/`, containing:

- `deploypro.dump`: the database, in `pg_restore` format.
- `volumes/<volume>.tar.gz`: every project's volume.
- `manifest.json`: the size and SHA-256 of each file.

A backup is written under a `.partial` name and renamed when complete, so an
interrupted one never looks finished.

**The master key is not in the backup.** Every environment variable in the
dump is encrypted with `DEPLOYPRO_MASTER_KEY`. A backup that carried the key would
carry every secret, readable. Keep the key in a password manager. Without it
the variables in a restored database cannot be decrypted.

**Copy backups off the machine.** A backup on the disk that fails does not
survive the failure. `rclone` or `rsync` from `DEPLOYPRO_BACKUP_HOST_DIR` on a cron
is enough.

**Restoring a volume:**

```bash
deploypro restore-volume /var/backups/deploypro/deploypro-20260923T030000Z balancevid recordings
deploypro deploy balancevid
```

This writes the backup's files into the volume, recreating the volume if it is
gone. Files that exist only in the live volume are kept. Stop the project's
workers first if they could be writing the same files.

**Restoring the database**, onto a new host or after losing the old one:

```bash
docker compose stop api worker
docker compose exec -T postgres dropdb -U deploypro deploypro
docker compose exec -T postgres createdb -U deploypro deploypro
docker compose exec -T postgres pg_restore -U deploypro -d deploypro --no-owner < deploypro.dump
docker compose start api worker        # with the same DEPLOYPRO_MASTER_KEY
```

The end-to-end run rehearses the disaster. It deletes a project's volume and
every container, restores the volume, redeploys, and checks the recordings are
back and still writable. It also restores the dump into an empty database.

### Cleaning up

Every hour, between deploys, the worker:

- **Removes old images.** It keeps the image of production, of any deployment
  with a running container, of anything queued or building, and of the newest
  `DEPLOYPRO_KEEP_IMAGES` ready deployments (10 by default). An older deployment
  stays listed and can be redeployed, which rebuilds it. Rolling back to it
  directly says exactly that.
- **Prunes build cache** that nothing has used for a week.
- **Deletes build logs and job runs** older than `DEPLOYPRO_LOG_RETENTION_DAYS`
  (30 by default). It always keeps the log of what is serving production, and
  each job's most recent run.

It only touches images this installation built, identified by a
`deploypro.instance` label. A second DeployPro on the same Docker host, such as a
staging copy or the end-to-end run, cannot clean away the first one's rollback
targets. `deploypro housekeeping` runs it now.

### Rollback

```bash
deploypro deployments blog
#  * #14   ready      live  9c8b1a22 main    blog-7e1a0d93
#    #13   ready      live  4f2a9c1e main    blog-3f9a2c71
#    #12   failed           2b7d4f01 main    blog-11c9e2a7

deploypro promote blog '#13'
```

## Reference

### API

The dashboard owns the root path; the JSON API lives under `/api`. All of it
except `/health`, `/ready` and `/webhooks/*` needs
`Authorization: Bearer $DEPLOYPRO_API_TOKEN` — or the dashboard's session cookie,
which is derived from the same token so a browser needs no second credential.

| | |
| --- | --- |
| `POST /api/projects` `GET /api/projects` | create and list |
| `GET PATCH DELETE /api/projects/{ref}` | `{ref}` is a slug or a uuid |
| `POST /api/projects/{ref}/deploy` | queue a deployment |
| `GET /api/projects/{ref}/deployments` | history |
| `GET PUT /api/projects/{ref}/env` · `DELETE …/env/{key}` | variables; values never come back out |
| `GET POST /api/projects/{ref}/domains` · `POST …/{host}/verify` | custom domains |
| `GET POST /api/projects/{ref}/volumes` · `DELETE …/volumes/{name}` | volumes; deleting keeps the data |
| `GET /api/deployments/{id}` | one deployment |
| `GET /api/deployments/{id}/logs` · `/logs/stream` | paged, or server-sent events |
| `POST /api/deployments/{id}/promote` | promote or roll back |
| `POST /api/deployments/{id}/redeploy` | rebuild the same commit |
| `POST /api/deployments/{id}/cancel` | only while still queued |
| `GET POST /api/projects/{ref}/processes` | workers and scheduled jobs |
| `GET PATCH DELETE /api/processes/{id}` | one process |
| `GET /api/processes/{id}/runs` · `POST …/run` | run history, and trigger now |
| `POST /webhooks/{slug}` | git push, HMAC-signed |

### Telling DeployPro how to build

Detection runs most-explicit-first: a `Dockerfile`, then a `deploypro.json`, then
project settings, then framework signatures, then a bare `index.html`.

```json
{
  "framework": "next",
  "installCommand": "npm ci --legacy-peer-deps",
  "buildCommand": "build:production",
  "startCommand": "node dist/server.js",
  "port": 3000
}
```

Committing a `Dockerfile` always wins, and is how you deploy a language DeployPro
has no rule for. Its `EXPOSE` is read for the port.

### What DeployPro tells your app

`PORT`, `DEPLOYPRO_URL`, `DEPLOYPRO_DEPLOYMENT`, `DEPLOYPRO_GIT_SHA`, `DEPLOYPRO_ENV`
(`production` or `preview`). `DEPLOYPRO_URL` is how a preview build discovers the
hostname it cannot know at commit time — for canonical tags, OAuth redirects
and `og:image`.

## Security

- **Build secrets are mounted, never baked.** Build-time variables arrive
  through a BuildKit secret mount, which exists for one `RUN` and is in no
  layer. `ARG` would put every one of them in `docker history`.
- **Every generated image drops out of root**, and containers run with
  `--cap-drop=ALL`, `--security-opt=no-new-privileges`, a memory ceiling, a
  pids limit and capped logs.
- **No deployment publishes a host port.** Containers are reachable only
  through the router, on a shared private network.
- **Repository URLs are an allowlist.** `ext::` URLs make `git clone` execute
  a shell command; they are refused before git sees them.
- **Secrets compare in constant time**, both the API token and webhook
  signatures.
- **The control plane holds the Docker socket, which is equivalent to root on
  the host.** That is inherent to the design. Do not run anything else you
  don't trust on this machine, and do not expose the API without TLS.

## What is proven and what is not

Run `./scripts/check.sh` for ruff, the import contracts and the unit suite.
Run `./scripts/e2e/run.sh` on any host with Docker for the end-to-end run.

**Run end to end** (`scripts/e2e/run.sh`, Docker 29.3, Traefik
3.7.13, Postgres 16):
- a git push, cloned over HTTPS
- a build from the repository's own Dockerfile, the start, and the health check
- routing on the deployment's own URL, and on a production domain through the
  route file
- a volume written by the web process, the worker and a cron job, and never
  mounted into a preview
- a deploy during a render: the old worker finishes, and its replacement
  starts at once
- a stop timeout enforced within 5 seconds of its deadline
- rollback to a deployment whose container had been reclaimed, in about a
  second, with the workers rolling back too
- a forged webhook refused, and a signed one deployed
- the GitHub App: made from a manifest (a code without the dashboard's state
  refused), installed (an installation GitHub does not know refused), a
  private repository imported and deployed with a single-repository token, a
  signed push deployed, a forged one refused, and an uninstall noticed. The
  GitHub in this run is a stand-in (`scripts/e2e/fakegithub.py`); the real
  github.com has not been run against yet

Checked by hand: every container came back after the Docker daemon was
restarted, and DeployPro found nothing to repair.

**Private repositories, in the same run:**
- without its deploy key, the SSH git server refuses DeployPro, and the error
  says to add the key
- with the key, the private repository deploys
- no log line holds a private key, and no decrypted key is left on disk
- a server whose identity changed is refused, and production is untouched

**Phase 1 checks in the same run** (the suite passed ten runs in a row):
- alerts reach a webhook for a site that is down, and for its recovery, sent
  once rather than on every check
- a stopped worker, a failed production deploy and a worker killed at its
  timeout are all reported
- cleanup cut 8 images to 3, and production kept its image and kept serving
- a backup survived deleting the volume and every container: restored,
  redeployed, and the files came back still writable
- the database dump restores into an empty database

**Found by the Phase 1 run, and fixed:**
- Docker 29 reports a missing object as "no such object", in lowercase. DeployPro
  only recognised "No such container", so every "already gone, carry on" path
  raised instead. After containers were deleted by hand, the next deploy
  failed.
- A release that crashes on start was restarted by Docker's restart policy,
  which kept it looking "running", so the deploy waited out its full health
  timeout. It now notices the restarts and fails in about 2 seconds instead
  of 62.

**Found by the first end-to-end run, and fixed:**
- Traefik read none of DeployPro's route files, because they were named `.json`
  and its file provider reads only `.yml`, `.yaml` and `.toml`. No production
  domain, promotion or rollback would ever have reached a real router.
- The pinned `traefik:v3.1` cannot talk to Docker 29, so no deployment URL
  would have been routed at all on a current host.
- Draining failed on every image without a `STOPSIGNAL`, which is most of
  them, because Docker omits that field rather than leaving it empty.
- Stop deadlines could be overshot by a minute or more: the cleanup ran once a
  minute, on a loop that pauses during builds.

**Tested (215 tests, no daemon needed):** detection across nine stacks and its
tie-breaks, including that a commented-out `output: 'standalone'` is not read
as enabled; image invariants over every generator (non-root, multi-stage, no
`ARG`, dependency layer before source); DNS label safety and non-enumerable
deployment ids; shell quoting proved by round-tripping through a real `sh`;
multi-line values kept out of the env file; `0600` on both secret files;
router JSON including priority, canonical redirect and path preservation;
encryption round-trip and loud failure on a rotated key; webhook signature
rejection and push filtering; API auth on every management route; mount path
validation; the exact `docker` arguments for mounts, stop timeouts, draining
(rename, then signal by id) and the draining sweep.

**Run against a real Postgres 16:** all three migrations, and the volume and
stop-timeout commands and API routes, including the database's own rejection
of a duplicate mount path and a comma in a path.

**Installed for real** (Hetzner CX23, Ubuntu 24.04, Docker 29.8, Cloudflare
DNS), using `scripts/install.sh`:
- DeployPro's own image builds and `docker compose up` starts it
- migrations apply
- `deploypro doctor` is clean
- the wildcard certificate for `*.deploys.<domain>` is issued by Let's Encrypt
  over DNS-01 through Cloudflare

**Found by that first real install, and fixed:**
- `migrate` looked for its files next to the installed package, found none
  in the image, and reported an empty database as up to date. The image now
  names the directory, and finding no migrations is an error.
- Routers labelled `tls=true` opted out of the entrypoint's wildcard
  certificate. Traefik applies that default only when a router's TLS is
  unset, so no certificate was ever requested and Traefik's self-signed one
  was served. The routers now carry no TLS label.

**Not yet exercised:**
- Per-domain certificates over HTTP-01, for custom domains.
- The framework Dockerfiles DeployPro generates (Next.js, Django, Go and the
  rest) against their real base images. The end-to-end run uses the
  repository's own `FROM scratch` Dockerfile.

**Not built:** metrics and graphs; multi-node scheduling; off-site backup
copies (use rclone or rsync); restoring the database from the CLI (the README
gives the four commands).

## Layout

```
deploypro/
  domain/        pure: detection, Dockerfile generation, naming, models
  adapters/      Postgres, Docker CLI, git, Traefik files, encryption
  repositories/  queries, including the deployment queue
  engine/        the pipeline, promotion, health, environment, routing,
                 processes and the schedule sweep
  routers/       HTTP (the JSON API)
  web/           the dashboard: pages, form handlers, derived states (views.py),
                 templates, one stylesheet, one script, self-hosted fonts
  worker.py      the deploy loop
  cli.py         the operator's tool
db/migrations/   schema
```

Three import contracts are enforced in `scripts/check.sh`: the domain layer
imports no vendor, only the engine drives Docker and git, and the layers run
one way. They fail the build rather than waiting for a reviewer.
