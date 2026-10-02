# DeployPro — Amendment to Phase 5 §2: the Overview shows the application

**Status:** **APPROVED 2026-10-02 by the owner** ("follow your recommendation and also use this benchmark image") and implemented.
**Amends (locked):** `deploypro-phase5-page-layouts.md` §2, the Project Overview. Nothing else in Phases 2–5 changes.
**Benchmark:** the owner's mock-up of a Vercel-style project page:
- a header with the address, repository, branch and health
- a large preview of the site with Desktop / Tablet / Mobile, **Open in new tab** and **Refresh**
- a production panel with **Open live site** and **Redeploy**
- stat tiles
- project details and domains
- a deployments table with **Open** per row

**Governing order (owner):** *What is running → what it looks like → which deployment produced it → what infrastructure supports it.*

---

## 1. Layout (desktop)

```
BalanceVid                                              ● HEALTHY
balancevid.deploypro.us ↗ · ICOFCUCAM/balancevid · main · PRODUCTION    deployed 4 min ago
[ Needs attention — only when there is something ]
┌──────────────────────────────────────────────┐ ┌───────────────────────┐
│                                              │ │ PRODUCTION            │
│        screenshot of the live site           │ │ ● SERVING             │
│                                              │ │ #276 · 9825c349 · main│
│                                              │ │ ready 10:41 (4 min)   │
│                                              │ │ [ Open live site ↗ ]  │  primary
├──────────────────────────────────────────────┤ │ [ Deploy main ]       │  secondary
│ (Desktop) (Mobile)   captured 10:41 · #276   │ │ Roll back to #274     │
│               Open in new tab ↗   ⟳ Refresh  │ │ Quick links           │
└──────────────────────────────────────────────┘ └───────────────────────┘
 SOURCE ✓ ── BUILD ✓ ── DEPLOY ✓ ── ◆ PRODUCTION          (the deployment line)
┌ Deployments ┐ ┌ Average build ┐ ┌ Success rate ┐
│ 12 · 7 days │ │ 4m 12s · 7 d  │ │ 92% · 7 days │
└─────────────┘ └───────────────┘ └──────────────┘
┌ Project details ───────────┐ ┌ Domains ───────────────────────┐
│ Repository · Branch · Build│ │ balancevid.deploypro.us ● …    │
│ Created                    │ │ Manage domains →               │
└────────────────────────────┘ └────────────────────────────────┘
Runtime (Web · Workers · Jobs) — unchanged
Recent deployments: table, # · commit · branch · status · when · build time · Open ↗
```

On mobile everything stacks in that order. The preview keeps its aspect ratio.

## 2. Decisions taken (the owner accepted the recommendations)

- **Desktop and Mobile, not Tablet.** Each size is a separate capture with its own cost, and tablet rarely shows what the other two don't. The toggle works without JavaScript.
- **A picture, not an embedded site.** **Open in new tab** opens the real thing. The picture always says when it was taken and from which deployment, so a stale picture is never read as live.
- **Captured only when production changes** (promotion or rollback), or on **Refresh**. Never for previews.
- **Captured through the public address:** the primary verified domain if there is one, else the deployment's own address. So it shows what a visitor gets, HTTPS included.
- **Isolated and capped:**
  - one short-lived Chromium container per size, 512 MB memory cap, 45 s limit, removed afterwards
  - one capture at a time
  - not on DeployPro's network, so it has no route to the database or Docker: it is a visitor and nothing more
- **"Open" on a deployment row only when its container is running.** A stopped deployment's address serves nothing.
- **Open live site is the panel's primary action; Deploy {branch} becomes secondary.** This follows the benchmark: on a healthy project, looking is the common action. Every non-serving state keeps its Phase 3 primary (Deploy, Retry and so on), and one primary per view still holds.

## 3. The benchmark's elements, and which are real

| Benchmark element | Here | Why |
|---|---|---|
| Preview, Desktop/Mobile, Open, Refresh | **Yes** | §2 |
| Production panel, Open live site, Redeploy, Roll back | **Yes** | |
| Quick links (preview of current deployment, environment, logs, settings) | **Yes** | They link to existing pages |
| Stat: deployments (7 days) | **Yes** | Recorded |
| Stat: average build time (7 days) | **Yes** | started → built, recorded |
| Stat: success rate (7 days) | **Yes** | In place of the benchmark's "Uptime" |
| Stat: response time, uptime % | **No** | Not recorded. The monitor checks every minute but keeps no history. A number nobody measured would be invented. |
| Project details: repository, branch, build (framework), created | **Yes** | |
| Region, IP, SSH key, firewall | **No** | One server; region and firewall are not things DeployPro knows. The System page has the server's facts. |
| Global sidebar, search, notifications, environment switcher, Logs/Monitoring tabs | **No, not in this amendment** | Each is a feature without a backend today. Logs per deployment already exist on the deployment page. |

## 4. States of the preview

| State | Shows |
|---|---|
| Captured | The image, *"captured 10:41 · #276"* |
| Capturing (requested, running) | The last image if there is one, dimmed, with *"Capturing preview…"*; the page refreshes itself (Q-S2: something is in progress) |
| Never captured | A quiet frame: *"No preview yet"* with **Capture preview** |
| Failed | The last image if any, with *"No new preview: {reason}"* and **Retry**. It never affects the deploy. |
| Nothing in production | No preview area; the Phase 3 first-deploy states stand |
| Previews off (`DEPLOYPRO_PREVIEW_IMAGE` empty) | No preview area |

## 5. Engine (implementation)

- `site_previews`: one row per project, with the deployment captured, status, times, error and the two file names.
- Promotion marks the row *requested*. The worker's job loop claims it, captures both sizes in turn, and writes PNGs to the `previews` volume. The dashboard serves them read-only to signed-in users.
- The capture image is `DEPLOYPRO_PREVIEW_IMAGE` (default `zenika/alpine-chrome`). It is about 670 MB on disk once, and it is pulled on first use.
