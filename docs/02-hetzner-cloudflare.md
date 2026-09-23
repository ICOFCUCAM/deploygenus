# Forge on Hetzner, with DNS on Cloudflare

From nothing to a live site in about 30 minutes. Throughout, replace:

- `example.com` with your domain (it must already be on Cloudflare)
- `deploys.example.com` with the domain Forge will own. Every deployment gets
  `<id>.deploys.example.com`, and the dashboard is `forge.deploys.example.com`.

## 1. Create the server (Hetzner Cloud)

In the [Hetzner Cloud console](https://console.hetzner.cloud):

1. **New project**, then **Add server**.
2. **Location:** the one nearest your users.
3. **Image:** Ubuntu 24.04.
4. **Type:** shared vCPU, x86. What to pick depends on what you run:
   - **CX22** (2 vCPU, 4 GB) is enough for websites and APIs.
   - **CX32 or larger** (4 vCPU, 8 GB and up) suits BalanceVid, or anything
     running ffmpeg. Rendering is CPU-bound, and builds share the same CPUs.
   - You can resize later: power off, Rescale, keep the disk.
5. **Networking:** keep public IPv4 on.
6. **SSH key:** add yours. Skip passwords.
7. **Firewalls:** create one allowing inbound **TCP 22, 80, 443** (and ICMP if
   you like), and attach it. Hetzner's firewall sits in front of the server,
   so nothing else can reach it.
8. **Backups:** optional. Forge makes its own backups every day, but Hetzner's
   are a second, off-disk copy of everything for 20% of the server price.
   Worth it.

Note the server's **IPv4 address**. It is `203.0.113.10` in the examples below.

## 2. Point DNS at it (Cloudflare)

In the Cloudflare dashboard, open your domain, then **DNS → Records**, and add:

| Type | Name | Content | Proxy status |
| --- | --- | --- | --- |
| A | `deploys` | `203.0.113.10` | **DNS only** (grey cloud) |
| A | `*.deploys` | `203.0.113.10` | **DNS only** (grey cloud) |

**Grey cloud, not orange.** Forge gets its own certificates and terminates
HTTPS itself. Behind Cloudflare's proxy, visitors would get Cloudflare's
certificate instead, and the free one covers `*.example.com` but not a
second-level name like `*.deploys.example.com`, so every deployment URL would
show a certificate error.

## 3. Create a Cloudflare API token

Forge proves to Let's Encrypt that it controls `*.deploys.example.com` by
writing a temporary DNS record. That is the only way to get a wildcard
certificate, and it needs a token.

**My Profile → API Tokens → Create Token → Custom token**:

- **Permissions:**
  - Zone → Zone → **Read**
  - Zone → DNS → **Edit**
- **Zone resources:** Include → Specific zone → `example.com`

Create it and copy the token. Cloudflare shows it only once.

## 4. Install Forge

SSH in and run the installer:

```bash
ssh root@203.0.113.10

apt-get update && apt-get install -y git
git clone https://github.com/ICOFCUCAM/deploygenus.git /opt/forge
cd /opt/forge

FORGE_DEPLOY_DOMAIN=deploys.example.com \
FORGE_ACME_EMAIL=you@example.com \
CF_DNS_API_TOKEN=paste-the-token-here \
bash scripts/install.sh
```

It installs Docker and generates the secrets. It checks that both DNS records
point at this server and starts everything. Then it prints the dashboard
address. Nothing is asked twice: running it again later is how you upgrade.

> Until the Forge changes are merged into `main`, add
> `FORGE_BRANCH=claude/tender-archimedes-libref` to the command.

## 5. Right after it finishes

1. **Save the master key.**
   `grep FORGE_MASTER_KEY /opt/forge/.env` prints it. Put it in your password
   manager. Backups deliberately do not contain it, and without it a restored
   database's variables cannot be read.
2. **Sign in.** Open `https://forge.deploys.example.com` and use the
   `FORGE_API_TOKEN` from `/opt/forge/.env`. The first certificate can take a
   minute to arrive; until then the browser warns about it.
3. **Alerts.** Create a Slack or Discord incoming webhook. Set
   `FORGE_ALERT_WEBHOOK_URL=` in `/opt/forge/.env`, then:

   ```bash
   cd /opt/forge && docker compose up -d && forge test-alert
   ```

4. **Prove the whole platform on this server** (about 3 minutes, and it
   cleans up after itself):

   ```bash
   apt-get install -y golang-go postgresql python3-venv
   cd /opt/forge && python3 -m venv .venv && .venv/bin/pip install -q -e '.[dev]'
   ./scripts/e2e/run.sh
   ```

   It runs its 65 checks on this server's own Docker, next to the real
   installation, touching nothing of it.

## 6. Deploy BalanceVid

```bash
forge project create --name BalanceVid --repo https://github.com/you/balancevid.git
forge volume add balancevid recordings /data
forge process add balancevid render --type worker --command "node worker.js"
forge project set balancevid --stop-timeout 1800 --memory 4096 --cpus 3
forge env set balancevid DATABASE_URL '…' --target production
forge deploy balancevid
forge logs <the id it prints> --follow
```

Then connect pushes: `forge webhook balancevid` prints the URL and secret for
the repository's **Settings → Webhooks** on GitHub.

Its Dockerfile must create `/data` and give it to the user the app runs as.
See the README, under Volumes.

**Your own domain for it**, such as `app.example.com`:

1. In Cloudflare, add `A app → 203.0.113.10`, **DNS only**.
2. Then:

   ```bash
   forge domain add balancevid app.example.com --primary
   forge domain verify balancevid app.example.com
   ```

## 7. Copy backups off the server

Daily backups land in `/var/backups/forge`, on the same disk they protect.
Copy them somewhere else. Cloudflare R2 (no egress fees) with `rclone` is a
good fit:

```bash
apt-get install -y rclone
rclone config            # add an "r2" remote: S3-compatible, provider Cloudflare
crontab -e               # then add:
30 4 * * * rclone sync /var/backups/forge r2:forge-backups --quiet
```

## When something is wrong

| Symptom | Look at |
| --- | --- |
| Browser warns about the certificate for more than a few minutes | `docker compose logs router \| grep -i acme`. Usually the token's permissions, or an orange-cloud record. |
| `forge doctor` says the wildcard does not resolve | The `*.deploys` record, and that it is DNS only |
| A deploy fails | `forge logs <id>`. The last lines are the container's own output. |
| Nothing reachable at all | The Hetzner firewall allows 80 and 443, and `docker compose ps` shows everything up |

Upgrade Forge: `cd /opt/forge && bash scripts/install.sh`.
