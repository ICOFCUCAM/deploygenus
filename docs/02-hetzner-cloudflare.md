# DeployPro on Hetzner, with DNS on Cloudflare

From nothing to a live site in about 30 minutes. Throughout, replace:

- `example.com` with your domain (it must already be on Cloudflare)
- `deploys.example.com` with the domain DeployPro will own. Every deployment gets
  `<id>.deploys.example.com`, and the dashboard is `deploypro.deploys.example.com`.

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
8. **Backups:** optional. DeployPro makes its own backups every day, but Hetzner's
   are a second, off-disk copy of everything for 20% of the server price.
   Worth it.

Note the server's **IPv4 address**. It is `203.0.113.10` in the examples below.

## 2. Point DNS at it (Cloudflare)

In the Cloudflare dashboard, open your domain, then **DNS → Records**, and add:

| Type | Name | Content | Proxy status |
| --- | --- | --- | --- |
| A | `deploys` | `203.0.113.10` | **DNS only** (grey cloud) |
| A | `*.deploys` | `203.0.113.10` | **DNS only** (grey cloud) |

**Grey cloud, not orange.** DeployPro gets its own certificates and terminates
HTTPS itself. Behind Cloudflare's proxy, visitors would get Cloudflare's
certificate instead, and the free one covers `*.example.com` but not a
second-level name like `*.deploys.example.com`, so every deployment URL would
show a certificate error.

## 3. Create a Cloudflare API token

DeployPro proves to Let's Encrypt that it controls `*.deploys.example.com` by
writing a temporary DNS record. That is the only way to get a wildcard
certificate, and it needs a token.

**My Profile → API Tokens → Create Token → Custom token**:

- **Permissions:**
  - Zone → Zone → **Read**
  - Zone → DNS → **Edit**
- **Zone resources:** Include → Specific zone → `example.com`

Create it and copy the token. Cloudflare shows it only once.

## 4. Install DeployPro

SSH in and run the installer:

```bash
ssh root@203.0.113.10

apt-get update && apt-get install -y git
git clone https://github.com/ICOFCUCAM/deploygenus.git /opt/deploypro
cd /opt/deploypro

DEPLOYPRO_DEPLOY_DOMAIN=deploys.example.com \
DEPLOYPRO_ACME_EMAIL=you@example.com \
CF_DNS_API_TOKEN=paste-the-token-here \
bash scripts/install.sh
```

It installs Docker and generates the secrets. It checks that both DNS records
point at this server and starts everything. Then it prints the dashboard
address. Nothing is asked twice: running it again later is how you upgrade.

> Until the DeployPro changes are merged into `main`, add
> `DEPLOYPRO_BRANCH=claude/tender-archimedes-libref` to the command.

## 5. Right after it finishes

1. **Save the master key.**
   `grep DEPLOYPRO_MASTER_KEY /opt/deploypro/.env` prints it. Put it in your password
   manager. Backups deliberately do not contain it, and without it a restored
   database's variables cannot be read.
2. **Sign in.** Open `https://deploypro.deploys.example.com` and use the
   `DEPLOYPRO_API_TOKEN` from `/opt/deploypro/.env`. The first certificate can take a
   minute to arrive; until then the browser warns about it.
3. **Alerts.** Create a Slack or Discord incoming webhook. Set
   `DEPLOYPRO_ALERT_WEBHOOK_URL=` in `/opt/deploypro/.env`, then:

   ```bash
   cd /opt/deploypro && docker compose up -d && deploypro test-alert
   ```

4. **Prove the whole platform on this server** (about 3 minutes, and it
   cleans up after itself):

   ```bash
   apt-get install -y golang-go postgresql python3-venv
   cd /opt/deploypro && python3 -m venv .venv && .venv/bin/pip install -q -e '.[dev]'
   ./scripts/e2e/run.sh
   ```

   It runs its 65 checks on this server's own Docker, next to the real
   installation, touching nothing of it.

## 6. Deploy BalanceVid

```bash
deploypro project create --name BalanceVid --repo git@github.com:you/balancevid.git
deploypro project key balancevid     # add what it prints on GitHub (see below)
deploypro volume add balancevid recordings /data
deploypro process add balancevid render --type worker --command "node worker.js"
deploypro project set balancevid --stop-timeout 1800 --memory 4096 --cpus 3
deploypro env set balancevid DATABASE_URL '…' --target production
deploypro deploy balancevid
deploypro logs <the id it prints> --follow
```

**The deploy key** lets DeployPro read a private repository and nothing else. On
GitHub, open the repository, then **Settings → Deploy keys → Add deploy key**.
Paste the line `deploypro project key` printed, and leave **Allow write
access** off.

Then connect pushes: `deploypro webhook balancevid` prints the URL and secret for
the repository's **Settings → Webhooks** on GitHub.

Its Dockerfile must create `/data` and give it to the user the app runs as.
See the README, under Volumes.

**Your own domain for it**, such as `app.example.com`:

1. In Cloudflare, add `A app → 203.0.113.10`, **DNS only**.
2. Then:

   ```bash
   deploypro domain add balancevid app.example.com --primary
   deploypro domain verify balancevid app.example.com
   ```

## When you make DeployPro's own repository private

The installer upgrades by pulling from GitHub, so once the repository is
private the server needs read access too. Give it a deploy key of its own:

```bash
ssh-keygen -t ed25519 -N '' -f /root/.ssh/deploypro_repo -C "deploypro server"
cat /root/.ssh/deploypro_repo.pub     # add as a read-only deploy key on the repo
cat >>/root/.ssh/config <<'EOF'
Host github.com
  IdentityFile /root/.ssh/deploypro_repo
  IdentitiesOnly yes
EOF
cd /opt/deploypro && git remote set-url origin git@github.com:ICOFCUCAM/deploygenus.git
git fetch      # answers "yes" once to trust GitHub, then works from then on
```

Upgrades then work as before: `bash scripts/install.sh`.

## 7. Copy backups off the server

Daily backups land in `/var/backups/deploypro`, on the same disk they protect.
Copy them somewhere else. Cloudflare R2 (no egress fees) with `rclone` is a
good fit:

```bash
apt-get install -y rclone
rclone config            # add an "r2" remote: S3-compatible, provider Cloudflare
crontab -e               # then add:
30 4 * * * rclone sync /var/backups/deploypro r2:deploypro-backups --quiet
```

## 8. The dashboard at your own domain

The dashboard is always at `https://deploypro.deploys.<your domain>`. To also
have it at the bare domain (`https://deploypro.us`), where visitors get the
sign-in page:

1. Cloudflare → DNS → **Add record**: type **A**, name **@**, IPv4 the
   server's IP, proxy **DNS only**.
2. On the server, add one line to `/opt/deploypro/.env`:
   ```bash
   echo 'DEPLOYPRO_DASHBOARD_DOMAIN=deploypro.us' >> /opt/deploypro/.env
   ```
3. Run the installer again: `cd /opt/deploypro && bash scripts/install.sh`.

The first visit can take a minute while the certificate is issued. Do this
before connecting GitHub (next step), so the GitHub App is created with this
address.

## 9. Connect GitHub

Dashboard → **New project → Connect GitHub → Create the app on GitHub**, then
on GitHub **Create GitHub App**, then **Install** on your account with the
repositories DeployPro may read. You come back to New project with those
repositories listed; **Import** deploys one.

Projects created before this (BalanceVid) are linked with one command, and
then deploy on every push:

```bash
deploypro github link balancevid
```

## When something is wrong

| Symptom | Look at |
| --- | --- |
| Browser warns about the certificate for more than a few minutes | `docker compose logs router \| grep -i acme`. Usually the token's permissions, or an orange-cloud record. |
| `deploypro doctor` says the wildcard does not resolve | The `*.deploys` record, and that it is DNS only |
| A deploy fails | `deploypro logs <id>`. The last lines are the container's own output. |
| Nothing reachable at all | The Hetzner firewall allows 80 and 443, and `docker compose ps` shows everything up |

Upgrade DeployPro: `cd /opt/deploypro && bash scripts/install.sh`.
