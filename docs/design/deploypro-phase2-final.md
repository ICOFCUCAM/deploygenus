# DeployPro: Phase 2, Product Architecture and Structural Design Direction

**Status: LOCKED (2026-09-24).** The Phase 2 foundation, as decided by the product owner and **validated against the codebase** (`ICOFCUCAM/deploygenus` @ `7cc2130`, engine frozen).

### Locked decisions (2026-09-24)
1. **Settings placement:**
   - memory, CPU, instant rollback count and graceful shutdown time → **Configuration → Build**
   - delete project → **the bottom of Configuration**
   - disconnect GitHub → **System → Settings**
2. **System → Settings** shows installation settings (from `.env`) **read-only** for now. Editing them is a later engine extension.
3. **Framework wording on the public site: "detected automatically".** Never "supported". Only Dockerfile-based apps are proven on a real server.
4. **"Degraded" is called "Needs attention"** everywhere in the product.

### Freeze (owner instruction, 2026-09-24)
**Deployment functionality and the current deployment page are frozen until Phase 6.** No rearranging, no status changes, no tabs, filters, terminology, icons, colours or JavaScript. The current page and its data are the **source material** for the redesign.
**Nothing here is implemented.** Phase 6 implements Phases 2–5 exactly as specified.

**Identity decision:** DeployPro has **its own visual identity**. It doesn't adopt BalanceVid's visual language. General principles (state by shape + word + colour, accessibility, showing only recorded truth) still apply, because they're right for DeployPro on their own merits.

**How to read the validation notes:**
- ✅ the engine supports this as specified
- ⚠ supported with a limit the design must respect
- ⛔ not supported by the engine; the design must not show it until engine work lands

**Phase map (renamed, locked):**

| Phase | What it covers |
|---|---|
| 2 | Architecture + structural design direction (this document) |
| 3 | UX states |
| 4 | Design system |
| 5 | Page composition |
| 6 | Implementation |

---

## 2A · Public vs private architecture ✅

```
deploypro.us  (www.deploypro.us → 301 to deploypro.us)
    │
    ├── Public DeployPro                 separate repository, deployed BY DeployPro
    │                                    "DeployPro deploys DeployPro"
    └── Sign in ──────────────→  app.deploypro.us
                                     │
                                     └── Private control plane   (this repository)
```

**Validation:**
- ✅ `app.deploypro.us` is live with its own certificate. deploypro.us and www redirect to it until the public site exists.
- ✅ The GitHub App is registered at `app.deploypro.us/github/webhook`, `/github/created` and `/github/installed`. **These addresses must never move to the public site.**
- ✅ Cutover, in this order:
  1. deploy the public project
  2. remove `deploypro.us,www.deploypro.us` from `DEPLOYPRO_DASHBOARD_DOMAIN` and run the installer
  3. add both addresses as the public project's domains
  
  Doing 3 before 2 makes two routes claim one hostname.
- ⚠ **The public site has no access to the control plane**, shows no owner data, and makes only claims the engine supports (see 2F).

---

## 2B · Primary navigation (locked)

```
DEPLOYPRO                         (global)
  Projects
  ────────────────
  System
    Settings

PROJECT                           (inside a project)
  Overview
  Deployments
  Runtime
    Workers
    Jobs
  Configuration
    Environment
    Domains
    Storage
    Build
    Repository
```

**Logs is hidden** until runtime logs exist. **Every item is its own URL**: the dashboard must keep working without JavaScript.

**URL structure:**

| Item | Address |
|---|---|
| Projects | `/` |
| New project | `/projects/new` |
| System → Settings | `/system` |
| Overview | `/projects/{p}` |
| Deployments | `/projects/{p}/deployments` |
| One deployment | `/deployments/{id}` (unchanged: the CLI, alerts and logs print it) |
| Runtime → Workers | `/projects/{p}/runtime/workers` (+ `/{name}`) |
| Runtime → Jobs | `/projects/{p}/runtime/jobs` (+ `/{name}`) |
| Configuration → … | `/projects/{p}/config/environment · domains · storage · build · repository` |

Old worker/job pages (`/projects/{p}/processes/{name}`) redirect to the new ones.

**Validation findings the owner must place (no decision is made here):**

| Finding | Detail |
|---|---|
| ✅ **Placed by decision 1:** five things had no home in the structure | the project's **memory/CPU**, **instant rollback count** (`keep_warm`), **graceful shutdown time** (`stop_timeout_seconds`), **delete project**, and the GitHub App's **disconnect**. The first four belong to one project; disconnect belongs to the installation. **Resolved:** memory/CPU, rollback count and shutdown time → **Build**; delete → the foot of **Configuration**; disconnect GitHub → **System → Settings**. |
| ⚠ **What "System → Settings" can actually hold** | The installation's configurable values (alerts webhook, backup hour, dashboard address, image retention) live in `/opt/deploypro/.env`. The dashboard can **show** them but not change them without new engine work. Things it *can* act on today: the GitHub App (connect/disconnect, choose repositories). Things available from engine functions not yet exposed (**[small]**): the `doctor` checks, a test alert, a manual backup. So Settings starts as mostly **read-only status plus the GitHub App**, and says so. |
| ✅ Runtime → Workers / Jobs | Both exist in the engine as `processes` (types `worker` and `cron`). Jobs also have `job_runs` history. |

---

## 2C · What every page owns

**One question per page. One owner per engine object. No page controls something another page owns.**

| Page | Question | Contains | Primary action | Does NOT contain |
|---|---|---|---|---|
| **Projects** | Does anything need me? | each project's state, address, production deployment, and latest deployment if it differs | New project | configuration |
| **Overview** | How is my application doing right now? | production state · current production deployment · recent deployment activity · runtime state · workers/jobs summary · things needing attention | Deploy {branch} | any configuration form |
| **Deployments** | What has been deployed and what happened? | history · production/preview distinction · status · commit · branch · trigger · duration → deployment detail | none (rows open deployments) | runtime controls |
| **Deployment** (detail) | What happened to this deployment? | status, identity, the lifecycle line, the stored error, the build log, details | the one action its state allows | configuration |
| **Runtime** | What is running? | web service, workers summary, jobs summary | none | configuration |
| **Workers** | What long-running processes are running? | each worker's state, what it runs, how it's replaced on deploy | Add worker / Pause | job history |
| **Jobs** | What scheduled executions are happening? | each job's schedule, last outcome, next run, run history with output | Add job / Run now | worker controls |
| **Configuration** | How is this application configured? | index of the five sub-pages with one line of state each; **Delete project at the bottom** | none | runtime state |
| **Environment** | What variables does it use? | variables by scope; variables DeployPro provides | Add variable | values (never shown) |
| **Domains** | Where is it reachable? | domains, verification, primary, redirects, DNS instructions | Add domain | none |
| **Storage** | What persistent data does it use? | mounts, who sees them, backup inclusion | Add mount | none |
| **Build** | How is it built and run? | framework (detected/overridden), root directory, commands, port · **resources:** memory, CPU · **deployment behaviour:** instant rollback count, graceful shutdown time | Save | none |
| **Repository** | Where does it come from? | repository, production branch, how DeployPro reads it, how pushes arrive (GitHub App / webhook / deploy key) | Link to GitHub | installation-wide GitHub settings |
| **System → Settings** | What is happening with DeployPro itself? | GitHub App (incl. **Disconnect**), installation status, installation settings **read-only** | depends on the item | anything project-specific |

**Validation against the Overview list:**

| Item | Engine support |
|---|---|
| Production state | ✅ serving / container missing / never deployed |
| Current production deployment | ✅ |
| Recent activity | ✅ derived from deployments and job runs |
| Runtime health | ⚠ **running / missing only.** "Is the site answering" is checked by the monitor but **not stored**, so it can't be shown yet ⛔ |
| Workers/jobs summary | ✅ definitions and job outcomes. Worker *running counts* ⚠ need a Docker read (**[small]**). |
| Important alerts | ⛔ **Alerts are sent to a webhook, not stored.** The Overview can show *conditions needing attention*, derived from recorded data, but not an alert history. |

**Validation against the Deployments list:** ✅ status, branch, commit, trigger, duration. ⚠ **Commit message and author exist only for push-triggered deployments.** Dashboard, CLI and import deploys store none (the engine discards them after cloning, **[small]**). The design must not rely on a message being present.

---

## 2D · Visual hierarchy (before styling)

Four levels, in this order of visual importance:

| Level | What | Examples | Rule |
|---|---|---|---|
| **1 · State** | what is true right now | ● Production · Building · Failed · Healthy · Needs attention | **Dominates.** Seen first, readable at a glance and in greyscale. |
| **2 · Identity** | which thing this is | BalanceVid · Deployment #12 · 8f31a2c · main | Directly under or beside the state. |
| **3 · Action** | what you can do about it | Deploy · Open · Roll back · Redeploy · Run now | One primary action per view. Destructive actions never primary. |
| **4 · Technical detail** | how it's implemented | image · internal port · container · memory · CPU | **Available, never in the way.** Behind "Details". |

Between levels 3 and 4 sits **operational information** (durations, schedules, next run, counts). It's visible by default, but quieter than state, identity and action.

**Validation of the Level 1 vocabulary:**
- ✅ **Production, Building, Failed:** recorded directly.
- ⚠ **Healthy:** the engine doesn't store health checks. "Healthy" may only mean *derived*: production is running, workers are at their wanted count, and the last job runs succeeded. It must never imply "the site answered just now" until health results are stored ⛔.
- ⚠ **Degraded:** there's no such engine state. It's derived from four recorded conditions:
  - production container missing
  - fewer workers running than wanted
  - the last job run failed or timed out
  - the latest deployment failed while production is fine
  
  **Product word: "Needs attention"** (locked). Phase 3 fixes the rule.

**Terms that must not reach levels 1–3** (they may appear under "Details" only):
- `keep_warm` → *keep N running for instant rollback*
- `reclaimed` → *Stopped · restarts in seconds on rollback*
- `drain` → *graceful shutdown / finishing its work*
- `short_id` → *#N*
- `container_id`, `image_tag` → Details only
- `promote` → *Make production* (or *Roll back to #N* when older)
- `process` → *Worker* / *Job*
- `cron` → *Job*
- `volume` → *Storage / mount*

---

## 2E · Visual personality

> **Infrastructure, made clear.**
> precise · calm · technical · confident · deliberate
> *A system that is under control.*

**It is not:**
- generic SaaS
- a generic developer tool
- a Vercel imitation
- a futuristic AI dashboard
- heavy glass
- gradient-heavy startup styling

**What the personality means in practice** (direction for Phase 4, not tokens):

| Trait | Means | Rules out |
|---|---|---|
| Precise | exact values (real durations, real commits, real times), aligned numerals, identifiers in a monospace | rounded-off vagueness ("a few moments ago" with no exact time available), decorative numbers |
| Calm | quiet surfaces; colour reserved for state; one accent at most | colour as decoration, competing highlights, alarm styling for ordinary events |
| Technical | honest names for real things, available at Level 4 | mystifying the system; hiding what it did |
| Confident | states said plainly ("Production was not touched") | hedging copy, apologetic empty states |
| Deliberate | every element has a job; nothing moves without meaning | ambient animation, idle motion, filler illustration |

**Validation:** ✅ Nothing in the personality needs data the engine lacks. It constrains the form only.

---

## 2F · One identity, two densities

**Shared across public and dashboard:**
- typography
- colour vocabulary
- iconography
- visual geometry
- the status language (2H)
- the signature line (2G)

**Different:** density and purpose.

| | Public DeployPro | Dashboard |
|---|---|---|
| Voice | **Deploy on hardware you own.** | **What is running?** |
| Density | low; one idea per screen | high; many records per screen |
| Job | explain, demonstrate, persuade, document | operate, observe, configure, recover |
| Tells the story of | Git deployment · previews · production · rollback · workers · jobs · persistent storage · automatic HTTPS | the owner's actual projects |
| Signature line used as | storytelling: the flow drawn large, explained step by step | instrumentation: small, exact, per deployment |

**Validation of the public story:** every item in the list is ✅ supported and proven on the real server, except:
- ✅ **Framework wording (locked):** "**detected automatically**" for Next.js, Vite, Nuxt, Astro, SvelteKit, Remix, Create React App, Node, Python, Go and static sites. Never "supported". Only Dockerfile builds are proven on real servers.
- ⛔ **The public site must not claim:** preview protection, teams or multiple users, metrics, runtime logs, serverless or edge functions, multiple servers.

---

## 2G · The signature visual idea: the deployment line

The flow the engine already runs becomes DeployPro's visual language:

```
SOURCE → BUILD → DEPLOY → HEALTH → PRODUCTION          PRODUCTION ⇄ ROLLBACK
```

- **Not a giant flowchart everywhere.** Lines, transitions, progression and state recur quietly.
- **The meaning it carries:** *something enters DeployPro, becomes a deployment, becomes runtime, becomes production.*
- **Where it appears (direction; composition is Phase 5):**
  - a deployment's detail page: the full line, one node per stage, with times
  - list rows: a compressed track showing how far each deployment got
  - the Overview: production as the line's endpoint, and rollback as the line returning to an earlier deployment
  - Workers during a deploy: the old and new worker side by side while the old one finishes
  - the public site: the same line drawn large, as the explanation

**Validation (this decides what the line may draw):**

| Node | Engine record | Status |
|---|---|---|
| SOURCE | `created_at`, commit, branch | ✅ |
| BUILD | `started_at` → `built_at` | ✅ |
| DEPLOY | `built_at` → `ready_at` | ✅ |
| HEALTH | only a log line ("healthy after 1.9s (1 attempt)") | ⚠ **not a recorded step.** Until the engine records it (**[small]**), HEALTH is drawn *as part of DEPLOY*, never as its own completed node with its own time. |
| PRODUCTION | the project's production pointer; the switch time isn't recorded | ⚠ shown as the **current-production marker**, not as a timed node |
| ROLLBACK | trigger `rollback` / the pointer moving to an older ready deployment | ✅ as a **transition**, not a status |

**Structural rule for extension:** the line is a sequence of nodes, each tied to a recorded field. When the engine later records HEALTH and PRODUCTION times, those nodes gain their own times without redesigning the line.

**States of the line:**
- complete: continuous
- in progress: the active segment (a static form when reduced motion is on)
- failed: broken at the node that failed, with the error beneath
- cancelled: ended at SOURCE
- preview: ends at DEPLOY with a "preview" terminus, never reaching PRODUCTION

---

## 2H · Status language (principle locked)

**Every status carries three signals: shape + word + colour.** No status is colour alone, readable in greyscale.

**Illustrative, from the owner's direction:**
```
● READY     ◌ BUILDING     ◐ DEPLOYING     × FAILED     ○ CANCELLED
```

The exact shapes and colours are **Phase 4**. The validation below fixes **which statuses must exist**, so Phase 4 designs the complete set:

| Domain | Statuses (engine source) |
|---|---|
| Deployment outcome | **queued** (missing from the illustration) · building · deploying · ready · failed · cancelled |
| Deployment kind | production · preview |
| Current production | one marker, one deployment per project |
| Ready deployment's container | running (instant rollback) · stopped (restarts in seconds) · removed (redeploy needed; **[small]**) |
| Worker | running n/n · below wanted (**[small]**) · paused · finishing work (**[small]**) |
| Job run | pending · running · succeeded · failed · timed out · skipped |
| Domain | verified · waiting for DNS |
| Project (derived) | healthy · needs attention (degraded) · not deployed · *down* ⛔ (needs stored health) |

**Note for Phase 4:** in the illustration, ◌ BUILDING and ○ CANCELLED are close in shape, and QUEUED needs a mark too. Phase 4 must keep all deployment outcomes distinguishable **by shape alone**.

---

## 2I · Responsive philosophy

> **Desktop is not the source of truth. The information hierarchy (2D) is.**

- Every page is designed as its hierarchy first: state → identity → action → operational → technical. Phase 5 then decides how that hierarchy *arranges* at each width.
- **Mobile is a different arrangement, not a smaller one.** No table is squeezed to 390px; a table becomes a list of records.
- Type is sized for the narrower viewport. Long identifiers are shortened in the middle with the full value available, and never widen the page.
- Nothing is hover-only. Everything reachable by mouse is reachable by keyboard and touch.

**The deployment page on a phone** (owner's example, validated):
```
Deployment #12
● READY                      ← Level 1
8f31a2c · main · 8 min ago   ← Level 2 (commit message only if recorded)
[ Open ]  [ Roll back ]      ← Level 3 (Roll back only if an older ready deployment exists)
LINE
✓ Build
✓ Deploy                     ← includes the health check until it's recorded separately
◉ Production                 ← the current-production marker, when it applies
```

**Pages that most need a considered mobile form:** Overview, Deployment detail, Deployment history, Workers/Jobs, Configuration forms.

---

## Validation summary

**The locked architecture is compatible with the engine as it is.** Everything shown by default is backed by recorded data.

**Things Phases 3–5 must respect:**

| Area | Constraint |
|---|---|
| Health | "Healthy" is derived; nothing implies a live check ⛔ until results are stored |
| Alerts | not stored, so the Overview shows derived attention conditions, not alert history |
| Commit message | present only for push deployments |
| Worker running counts, graceful-shutdown countdown, "removed" deployments | need small engine reads (**[small]**) |
| The HEALTH and PRODUCTION nodes | part of DEPLOY / a marker, until the engine records their times |
| Runtime logs | no navigation, no panel |

**All four owner decisions are made** (see "Locked decisions" at the top).

## Carried into later phases (from the owner's review of the live deployment page, 2026-09-24)
- **Phase 3: three independent dimensions of a deployment. They must never collapse into one badge:**
  - **deployment state** (queued · building · deploying · ready · failed · cancelled)
  - **production relationship** (current production, or not)
  - **runtime state** of a ready deployment (running · stopped · removed)

  A deployment can be ready without being production. It can be production and later stopped (production missing). It can remain a valid rollback target while not running.
  ```
  READY
  ├── PRODUCTION     (relationship)
  ├── RUNNING        (runtime)
  ├── STOPPED        (runtime)
  └── REMOVED        (runtime)
  ```
- **Phase 5 design question:** the primary action today reads **"Deploy claude/adoring-mayer-yfghmg"**, which exposes a raw branch name as the main button. Should it be **Deploy**, with the source/branch shown or chosen separately, or does the contextual label earn its place? A design decision, not an engine bug.
- **Phase 4/5:** the current list is information-rich but visually undifferentiated. Identity, state, production relationship, source, message and runtime state are separated only by text position.
