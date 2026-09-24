# DeployPro — Phase 5: Page Layouts

**Status:** **LOCKED 2026-09-24.** The routes `/projects/{p}/runtime` and `/projects/{p}/config` were confirmed by the owner at locking. Nothing is implemented; Phase 6 implements Phases 2–5 exactly.
**Builds on (locked, not reopened):**
- `deploypro-phase2-final.md`: navigation, URLs, page ownership, hierarchy
- `deploypro-phase3-ux-states.md`: states, copy, action matrix
- `deploypro-phase4-design-system.md`: Quiet Infrastructure

**Code baseline:** `ICOFCUCAM/deploygenus` @ `7cc2130` (engine frozen).
**Governing rule (owner):** *Structure before decoration. Every page makes the current system state and the next useful action obvious.*

**Reading the wireframes:**
- Glyphs (`●`, `◆`, `×`, `✓`) are placeholders for the Phase 4 marks. The final geometry is a Phase 6 gate.
- Words in CAPITALS are sentence-case text styled with CSS.
- `[ Button ]` is a button; `Link →` is a quiet text link.
- **Bold** marks the one primary action.
- Right-aligned items drop below on narrow screens unless a mobile layout is given.

---

## 0. Frame and conventions (all dashboard pages)

### 0.1 Page frame

```
┌──────────────────────────────────────────────────────────────────────────┐
│ DEPLOYPRO        Projects   System                            Sign out  │  global bar
├──────────────────────────────────────────────────────────────────────────┤
│ Project nav        │  Page header: title · state · as of 14:02           │
│ (inside a project) │  Message (?ok / ?err), when present                 │
│                    │  Page body                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**Global bar:**
- the DEPLOYPRO wordmark links to Projects
- Projects · System (Phase 2 §2B)
- Sign out on the right
- the current section carries `aria-current="page"` plus a visible blue underline

**Project nav** (inside a project only) is the Phase 2 §2B tree, exactly:

```
BalanceVid                 ← project name (links to Overview)
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

- **Desktop (≥ 960px):** a left column about 200px wide. The content column has a maximum of about 1040px.
- **Below 960px:** the nav becomes a `<details>` disclosure at the top of the content, reading *"BalanceVid · Deployments ▾"*. It works without JavaScript and never scrolls sideways (Phase 2 §2I).
- Logs is absent: hidden, not disabled (Phase 2 §2B).

**Page header:**
- the page title (Plex Sans, page-title size)
- the page's own state mark, where it has one
- **"as of HH:MM"** in muted small text on the right (Phase 3 §11, Q-S2). Live regions say *live* instead.

**Messages (`?ok=` / `?err=`):**
- one line under the header, with a mark and the words in the success or failure colour family
- `role="status"` for ok, `role="alert"` for errors
- engine text verbatim (Phase 3 rule 7)
- Phase 3 §12: the message appears on the page whose form was submitted. See Phase 6 constraint P6-2.

**Structure, not cards (owner §5, Phase 4 §10):**
- sections are separated by a section heading and a hairline
- records are rows divided by hairlines
- the only contained surfaces are:
  - the production block on the Overview
  - the log
  - destructive-action zones

### 0.2 Conventions used on every page

| Convention | Rule | Source |
|---|---|---|
| Hierarchy inside any block | state → identity → action → operational → technical | Phase 2 §2D |
| Primary action | exactly one per view, chosen by state; destructive never primary | Phase 2 §2D, Phase 3, Phase 4 §11 |
| Records on mobile | a table becomes a list of records; no squeezed tables | Phase 2 §2I |
| Long identifiers | **shortened in the middle**, full value in the text and accessible name; never widens the page | Phase 2 §2I |
| Nothing hover-only | tooltips (`title`) are never the only way to see something | Phase 2 §2I |
| Missing capability | absent, not disabled, not "—" | Phase 3 rule 3 |
| Failure | always stated, never an empty list | Phase 3 rule 3 |
| In progress | always with a number (elapsed or count) | Phase 3 rule 5 |
| Confirmations | no-JavaScript: a `<details>` section or a section on the page, stating the consequence, holding the final button; replaces `confirm()` | Phase 3 §12 |
| Landmarks | `header`, `nav` (global), `nav` (project, labelled), `main`, a skip link to `main`; one `h1` per page | WCAG 2.4.1 |
| Times | exact time on the page; relative forms only beside an exact one | Phase 2 §2E "Precise" |

### 0.3 The `Deploy {branch}` button (owner direction 1)

- The label is **`Deploy {branch}`**, with the full branch name, everywhere the branch is known.
- For long branches the **visual** text is shortened in the middle (Phase 2 §2I). The server renders the label as two spans: the head, which truncates with an ellipsis, and the last 12 characters, which never shrink.

  ```
  [ Deploy claude/adoring-…mayer-yfghmg ]
  ```
- The button's text content remains the full string, so the **accessible name is complete** without `aria-label`.
- Because nothing may be hover-only, the full branch name is also printed, unshortened and wrapping, in the identity line of the same page.
- **Q-S1:** when a deployment of that branch is already queued or building, a line under the button reads *"#20 is already building — this will wait and build again after it."* The button is never silently disabled.

---

## 1. Projects — `/`

**Question:** *Does anything need me?* **Primary action:** **New project** (Phase 2 §2C).

### 1.1 Layout (desktop)

```
Projects                                                   as of 14:02
                                                        [ New project ]
─────────────────────────────────────────────────────────────────────────
▲ NEEDS ATTENTION   Worklog                          worklog.example.com ↗
                    Production #7 · 1c9e40ab · main · 3 days ago
                    #8 failed · 20 min ago — npm ERR! missing script: build
                    1 worker · jobs: 1 failing
─────────────────────────────────────────────────────────────────────────
● HEALTHY           BalanceVid                  balancevid.deploypro.us ↗
                    Production #19 · 8d3a2da8 · claude/adoring-…mayer-yfghmg · 2 h ago
                    2 workers · jobs: all succeeding
─────────────────────────────────────────────────────────────────────────
○ NOT DEPLOYED      Landing                No permanent address — add a domain
                    Not deployed yet
─────────────────────────────────────────────────────────────────────────

Activity
  14:01  BalanceVid   #19 went live
  13:40  Worklog      #8 failed
  03:00  Worklog      job cleanup failed
  …                                           (last 10, across projects)
```

**Record, in Phase 3 §2 order:**
1. derived project state (mark + word, Phase 4 §5)
2. name (the link to the Overview) and address (a separate external link)
3. production: #N · commit · branch · time
4. the latest deployment, only if it differs from production and isn't ready
5. workers: count of defined workers, no running claim until **[small]**
6. jobs: the worst recent run state

- **Domain condition (owner direction, "where relevant"):** only when a domain isn't verified, one extra line with the domain's own mark, e.g. `▲ app.example.com waiting for DNS`. It **does not change the project's derived state.** Phase 3 §6 defines Needs attention by four conditions, and domains aren't among them.
- **Order:** needs attention first, then most recently deployed.
- **Next action per project:** the record's name opens its Overview, where the one state-driven action lives. No action buttons appear in rows, so the page keeps one primary action (**New project**).
- **Activity (Q-S6):** the last 10 events across projects, below the list and quieter.
- **Refresh (Q-S2):** meta refresh only while any project has something in progress.

### 1.2 Empty (S-EMPTY-PROJECTS)

```
Projects
Nothing is deployed here yet.
[ Connect GitHub ]   or deploy from a Git URL →      (GitHub not connected)
[ Import a repository ]                              (GitHub connected)
```

### 1.3 Mobile

Each record stacks as: state · name · address · production line · latest line · workers/jobs line. **New project** sits in the header under the title. Activity follows the list.

### 1.4 New project — `/projects/new` (part of the Projects family)

In order:
1. **Import from GitHub:** a repository list and a search field. This is the primary action when connected.
2. **Deploy from a Git URL:** a quiet secondary form.
3. **The GitHub App card:** connection state.

States follow Phase 3 §13: GitHub unreachable → *"Couldn't reach GitHub: {reason}."*, and the URL form stays available. Never an empty list.

---

## 2. Project Overview — `/projects/{p}`

**Question:** *How is my application doing right now?* **Primary action:** state-driven (Phase 3 §3); usually **Deploy {branch}**.

### 2.1 Layout (desktop), state S-READY-PROD

```
BalanceVid                                   ● HEALTHY           as of 14:02
balancevid.deploypro.us ↗ · ICOFCUCAM/balancevid · production branch claude/adoring-mayer-yfghmg

┌─ Production ─────────────────────────────────────────────────────────────┐
│ ● SERVING                                                                │
│ #19 · 8d3a2da8 · Publication formats: one composition, four shapes…     │
│ Ready since 12:02 (2 h ago)                                              │
│ SOURCE ✓ ── BUILD ✓ ── DEPLOY ✓ ── PRODUCTION ◆          (compressed line) │
│                                                                          │
│ [ Deploy claude/adoring-…mayer-yfghmg ]     Roll back to #18 · instant   │
└──────────────────────────────────────────────────────────────────────────┘

Needs attention                                      (section only if any)
  ▲ cleanup failed at 03:00.                                     Jobs →

Activity
  #20 is building · 2m 04s — production switches when it passes its health check.
                                                           View #20 →

Runtime
  Web       ● Running · #19
  Workers   2 workers · follows production #19                   Workers →
  Jobs      cleanup  × Failed 03:00 · next 03:00                    Jobs →

Domains
  balancevid.deploypro.us   ● Verified · primary
  www.balancevid.app        ▲ Waiting for DNS                     Domains →

Recent deployments                                    All deployments →
  (the last 5 rows, same row as §3.1)

Configuration
  Environment · Domains · Storage · Build · Repository     (links only)
```

**Hierarchy (owner direction), with two placements added by validation:**
1. **Project identity:** page title, derived project state, address, repository, production branch (full, wrapping).
2. **Production condition:** the one contained block and the strongest weight on the page. It holds the production state (Phase 3 §3), identity, the compressed line and the state's primary action.
   - **Rollback control:** secondary, naming its target and speed (*instant* / *restarts in a few seconds*); absent when there's no candidate.
3. **Needs attention:** the Phase 3 §6 conditions, most serious first, each linking to where it's resolved. Placed directly under production because they are Level 1 state (Phase 2 §2D). Absent when nothing applies.
4. **Activity:** a newer deployment in progress or failed (Phase 3 §3 rows). This is where **View #N+1** lives when that state makes it primary.
5. **Runtime:** web · workers · jobs, one line each with its own state (Phase 3 §3 "Runtime summary").
6. **Domains:** each domain with its mark. Read-only; managed on Configuration → Domains.
7. **Recent deployments:** the last 5 rows plus a link to the list. Other recent activity (job runs) appears in the Runtime lines.
8. **Configuration:** **links only.** Phase 2 §2C says the Overview contains no configuration form, so owner item 7 is satisfied as navigation.

### 2.2 The production block by state (Phase 3 §3, exactly)

| State | Block headline | Primary action |
|---|---|---|
| S-EMPTY-DEPLOYS | *"Nothing is deployed yet."* · repository · production branch | **Deploy {branch}** |
| First deploy running | the deployment's mark + word + elapsed | **View deployment** (Q-S1 note on Deploy) |
| First deploy failed | *"The first deployment failed."* · step · error, first line · *"Nothing was serving, so nothing changed."* | **View deployment** (Deploy again: secondary) |
| S-READY-PROD | **Serving** (success) | **Deploy {branch}** |
| S-PROD-MISSING | **"Production is down: its container is gone."** (failure family; the project state reads Needs attention in amber, per Phase 4 §5) | **Roll back to #M** if there's a candidate, else **Redeploy #N** |
| Newer deploy in progress / failed | production block as S-READY-PROD; the Activity line carries the newer deployment | **View #N+1** (Deploy becomes secondary) |
| S-ROLLBACK (recent) | *"Rolled back to #19 · from #21"*, with the transition in the compressed line (`#21 → #19 ◆`) | **Deploy {branch}** |

**S-ROLLBACK timing:** Phase 3 lists "when", but the engine doesn't record when production moved (`promoted_at` is **[small]**). The block shows **no time** for the rollback until that lands. This is a finding, not a change to Phase 3's intent.

**After promote or rollback (C7):** the POST returns to this page. The block simply shows the new state, and the ok message reads *"Production is now #N ({commit}). #M is still available: roll forward any time."* (Phase 3 §7). There's no animation.

### 2.3 Mobile

In order:
1. title, state and address
2. the production block (the line becomes vertical; the primary action is full width under it; rollback on its own line)
3. Needs attention
4. Activity
5. Runtime
6. Domains
7. Recent deployments (records)
8. Configuration links

**Refresh (Q-S2):** meta refresh only while something is in progress.

---

## 3. Deployment family

### 3.1 Deployments (list) — `/projects/{p}/deployments`

**Question:** *What has been deployed and what happened?* **Primary action:** none; rows open deployments (Phase 2 §2C).

```
Deployments                                                as of 14:02
Showing the latest 25.
─────────────────────────────────────────────────────────────────────────────
#20  ● BUILDING   PRODUCTION BRANCH                          elapsed 2m 04s
     4be1c09a  claude/adoring-mayer-yfghmg
     Tighten the feed layout
     14:00
─────────────────────────────────────────────────────────────────────────────
#19  ● READY      PRODUCTION BRANCH   ◆ CURRENT PRODUCTION
     8d3a2da8  claude/adoring-mayer-yfghmg
     Publication formats: one composition, four shapes, and the meaning survives
     12:00 · 2m 18s · RUNNING
─────────────────────────────────────────────────────────────────────────────
#18  ● READY      PRODUCTION BRANCH
     77f0d2e1  claude/adoring-mayer-yfghmg                  (no message recorded:
     09:12 · 2m 01s · STOPPED                                slot not rendered)
─────────────────────────────────────────────────────────────────────────────
#17  × FAILED     PREVIEW
     03aa91c4  feature/player
     Wed 18:40 · 1m 12s · Failed at Building
```

- **Row content** is exactly Phase 4 §12. `#N` is the row's link. The compressed line is omitted in the list at mobile width; the words carry it.
- **Desktop grid columns:**
  1. `#N`
  2. state
  3. production relationship
  4. source (commit, branch, message)
  5. time · duration/elapsed · runtime (right-aligned, Mono numerals)
- **Failed rows** add *"Failed at {step}"* (derived, Phase 3 §5.1). The error itself lives on the detail page.
- **Kind** is derived from the current production branch (**[small]** caveat); no historical claim.
- Filters and pagination are **[small]** and absent.
- **Empty:** *"No deployments yet."* + **Deploy {branch}**.

### 3.2 Deployment (detail) — `/deployments/{id}`

**Question:** *What happened to this deployment?* **Primary action:** the one its state allows (Phase 3 §5.1).

**The primary expression of the DeployPro identity:**
- the header establishes state and identity
- the **deployment line** is the dominant structural element
- the **log** is the body

#### Layout (desktop), state S-READY-PROD

```
BalanceVid › Deployments › #19                                   as of 14:02

#19   ● READY   PRODUCTION BRANCH   ◆ CURRENT PRODUCTION   ● RUNNING
8d3a2da8 · claude/adoring-mayer-yfghmg · push
Publication formats: one composition, four shapes, and the meaning survives
Created 12:00:04 · took 2m 18s · balancevid-6a6b0b05.deploypro.us ↗
                                                            [ Redeploy ]

  SOURCE ─────────── BUILD ─────────── DEPLOY ─────────── PRODUCTION
  ✓ 12:00:04         ✓ 1m 52s          ✓ 26s               ◆ Current production
  queued 0s                            includes health check

Build log                                                        finished
┌──────────────────────────────────────────────────────────────────────────┐
│ …                                                                        │
│ healthy after 1.9s (1 attempt)                                           │
└──────────────────────────────────────────────────────────────────────────┘

▸ Details     framework · image · internal port · container · deployment id · trigger
```

**Header, in order** (owner direction; Phase 2 §2D):
1. **State row:** `#N` · outcome mark + word · **Production branch** / **Preview** · **Current production** (only when it is) · runtime (Ready only: Running / Stopped).
2. **Identity:**
   - short commit (Mono)
   - the **full** branch name (wrapping)
   - trigger word
   - the commit message **only when recorded**; otherwise the line is not rendered
3. **Operational:**
   - created time (exact)
   - **took {duration}** when finished (`started_at` → `finished_at`), or **elapsed {m:ss}** while in progress
   - the deployment's own address, **only when Ready and Running** (Phase 3 lists the address for running states only)
4. **Primary action** at the end of the header block (below it on narrow screens), from the table below.
   - **Open ↗** is a quiet link beside it when an address is shown.
   - **Redeploy** is secondary where Phase 3 lists it second.

**Primary action by state (Phase 3 §5.1, exactly):**

| State | Primary | Secondary |
|---|---|---|
| S-QUEUED | **Cancel** | — |
| S-BUILDING / S-DEPLOYING | none (cancel while building is **[engine]**) | — |
| S-READY-PROD (current production) | **Redeploy** | Open ↗ |
| Ready, newer than production (production branch or preview) | **Make production** | Redeploy · Open ↗ |
| Ready, older than production (production branch or preview) | **Roll back to #N** (this deployment's number) | Redeploy · Open ↗ |
| S-READY-STOPPED | as running, with *"restarts in a few seconds"* on the action | Redeploy |
| S-READY-REMOVED **[small]** | **Redeploy** only (fallback today: shown as Stopped; the refusal appears as S-FORM-ERROR) | — |
| S-FAILED / S-CANCELLED | **Redeploy** | — |

**Two validation notes on the table:**
- **Label.** The owner's example "Roll back to this" is written as Phase 3's label **Roll back to #N**, so every rollback control names its target (Phase 4 C10).
- **Preview row.** Phase 3 §5.1 lists "Ready · Preview" in the same state as other ready deployments, and the engine agrees: `promote()` accepts any ready deployment that isn't already production. Today's page offers "Promote to production" on previews too. The layout follows Phase 3 unchanged. "Newer/older" compares deployment numbers, as `is_older` does today.

#### The deployment line (Phase 4 §2, the anchor)

**Markup:**
- an ordered list: `<ol aria-label="Deployment lifecycle">`
- one `<li>` per node, with the node name and its state **as text**, e.g. *"Build: completed in 1m 52s"*
- `aria-current="step"` on the active node
- the connecting segments and marks are decorative (`aria-hidden`)

This makes it a state representation readable without sight, not a progress bar.

**Nodes and times:**

| Node | Label under it |
|---|---|
| SOURCE | created time; queue wait (`created_at` → `started_at`) |
| BUILD | `started_at` → `built_at` |
| DEPLOY | `built_at` → `ready_at` (or `finished_at` when failed), with "includes health check" |
| PRODUCTION | the marker only; no time |

**Every state:**

| State | Line |
|---|---|
| Queued | `SOURCE ● ── BUILD ○ ── DEPLOY ○ ── PRODUCTION ○` · *"Waiting for the build worker · 0m 40s"* (+ the building deployment's name when another is building) |
| Building | `SOURCE ✓ ── BUILD ● ── DEPLOY ○ ── PRODUCTION ○` |
| Deploying | `SOURCE ✓ ── BUILD ✓ ── DEPLOY ● ── PRODUCTION ○` · *"Starting the container and checking it answers."* |
| Ready · current production | `SOURCE ✓ ── BUILD ✓ ── DEPLOY ✓ ── PRODUCTION ◆` |
| Ready · production branch, not current | `SOURCE ✓ ── BUILD ✓ ── DEPLOY ✓ ── PRODUCTION ○ not current` |
| Ready · preview | `SOURCE ✓ ── BUILD ✓ ── DEPLOY ✓ ┤ PREVIEW` (ends at DEPLOY; no PRODUCTION node) |
| Failed at Building | `SOURCE ✓ ── BUILD × ╳ DEPLOY ○ ── PRODUCTION ○`; the stored error **verbatim** directly under BUILD; *"Production was not touched."* / *"This was a preview; production was not involved."* |
| Failed at Deploying | as above with × at DEPLOY; the log opens at the end, the app's own last output marked |
| Failed at Queued (abandoned) | × at SOURCE; the engine-written error verbatim |
| Cancelled | `SOURCE ✕ ┤` (the line ends at SOURCE) · *"Cancelled before it started building."* |

- A preview's line ends at DEPLOY with its preview end-point in every state. It never shows a PRODUCTION node.
- **Line segments** (Phase 2 §2G):
  - continuous = complete
  - broken after the failed node = failed
  - not reached = hairline
  - the active segment animates only while the engine reports building/deploying, and is static under reduced motion
- **Rollback** is not drawn on this page. It's a transition shown on the Overview (§2.2).

#### Below the line

- **The Phase 3 body sentence** for the state:
  - preview line: *"Preview: its own address and preview variables; no production storage, workers or jobs."*
  - S-READY-RUNNING / STOPPED sentences
- **Build log:** the main body.
  - It takes the full content width and is Mono, on a contained surface.
  - The header reads *Build log* and the live status: *live* / *reconnecting…* / *finished*.
  - Auto-follow happens only while the reader is at the bottom (existing behaviour).
  - For terminal states, the log is shown opened at the end.
- **Details:** `<details>`, closed by default (Level 4, Phase 2 §2D). It holds: framework (detected at build) · image tag · internal port · container id · deployment id (short id) · trigger.

#### Mobile

The Phase 4 §19 layout, exactly:
1. `#N`
2. state
3. relationship lines
4. runtime
5. commit · branch
6. message (if recorded)
7. the **vertical** line
8. the primary action (full width), with **Open ↗** beside it
9. the log
10. Details

The CSS `order` moves the action below the line. The line contains no focusable elements, so the focus order still matches the visual order.

### 3.3 The live deployment page: behaviour (owner direction 3)

**Current behaviour (read from the code):**
- The page renders the state known at load.
- `deploypro.js` streams new log lines over SSE (`/api/deployments/{id}/logs/stream`).
- It reloads the whole page only when the stream sends `done`, i.e. after a terminal state.
- **Between load and finish, the header and line don't change.** A deployment loaded while queued keeps saying Queued through building and deploying.
- **Finding:** Phase 3 §5.2 says those transitions "happen without user action". Today they don't; only the final one does. Phase 5 has to specify how the header keeps up.

**Specified behaviour:**

| Part | Behaviour |
|---|---|
| **Live log** | Continuous, on the existing SSE stream. Never interrupted by a state change. |
| **Lifecycle header + line** | Authoritative engine state. Updated **in place** when the engine's state changes, without reloading the document. |
| **Elapsed** | The server renders it. With JavaScript it ticks each second from the recorded start time (a number, never a spinner). |
| **Terminal state** | Unchanged: the existing reload on `done`. The log is complete by then, so the reload interrupts nothing and brings in the state's actions. |
| **Without JavaScript** | There's no live log, so a refresh interrupts nothing. `<noscript><meta http-equiv="refresh" content="10"></noscript>` in the head, **only while in progress**. That's valid HTML, and it never fires when scripting is on, so it can't restart the stream. |
| **Q-S2 meta refresh** | **Not used on the deployment page when JavaScript is on**, because it would restart the log. It stays on Projects and Overview as locked. |

**The smallest mechanism, and why it's truthful:**
- The stream endpoint already re-reads the deployment row on **every poll** (`current = await deployment_repo.get(...)`), only to decide when to send `done`.
- Phase 6 adds one event to the same stream: `event: state` with `{status, started_at, built_at, ready_at}`, sent when `status` differs from the last one sent.
- The script sets the line's `data-state` and swaps the state word, with no new request, no new endpoint and no second connection.
- **Scope:** web/API layer only. No schema or engine change, so the freeze is respected.
- **Truthfulness:** the header shows the state the engine has recorded, at most one poll interval (the stream's existing cadence) after it changes. If the stream drops, the header shows *reconnecting…* and doesn't guess.
- **Fallback, if Phase 6 finds the event can't be added cleanly:** keep today's behaviour exactly (header as of load + reload on `done`), and label the header *"as of HH:MM"* so the lag is honest. **No** meta refresh while the log is live.

---

## 4. Runtime — Workers and Jobs

Same visual grammar (rows, marks, words), but **operationally distinct from deployments** (owner direction):
- no deployment line
- no deployment outcome marks
- no commit/branch hierarchy

Workers and jobs use their own words (Phase 3 §8–9) in the shared semantic colour families.

### 4.1 Runtime — `/projects/{p}/runtime`

Phase 2 §2C defines a **Runtime** page (*"What is running?"*: web service, workers summary, jobs summary; no primary action). Phase 2 §2B's URL table lists only its two sub-pages.

**Route (locked by the owner):** `/projects/{p}/runtime`. It holds the three summary lines from Overview §2.1 in more detail, with Workers and Jobs as its sub-pages.

### 4.2 Workers — `/projects/{p}/runtime/workers`

**Primary:** **Add worker** (Phase 2 §2C).

```
Workers                                                    as of 14:02
                                                          [ Add worker ]
Workers run the production deployment's image. They follow production #19.
─────────────────────────────────────────────────────────────────────────
render      node dist/render.js      1 replica            Pause
            Follows production #19
─────────────────────────────────────────────────────────────────────────
mailer      node dist/mailer.js      1 replica            Resume
            ○ PAUSING: stops at the next deploy
            It keeps running until the next deployment.  Deploy claude/…yfghmg →
─────────────────────────────────────────────────────────────────────────
```

- **Row:**
  1. name (link to the detail page)
  2. command (Mono)
  3. replicas
  4. state words per Phase 3 §8
  5. Pause/Resume (secondary, no confirmation)
- **Until [small]:** the definition plus *"follows production #N"*. **Never a running count** (no "Running 1/1", no "Below wanted").
- **Finishing work / Below wanted:** absent until **[small]**.
- **Q-S4 copy is exact:** *"Pausing: stops at the next deploy"* / *"Resumes at the next deploy"*.
- **Add worker:** a form section at the foot (name, command, replicas, memory). The header button jumps to it (an in-page anchor, no JavaScript).
- **Empty / no production:** the Phase 3 §8 sentences.

**Worker detail** — `/projects/{p}/runtime/workers/{name}`:
- identity (name, command)
- state
- *"How it's replaced on deploy"*: the old worker finishes its work for up to {graceful shutdown time}, a value set on Build
- Pause/Resume
- **Remove worker** in a separated zone, with a `<details>` confirmation: *"Its run history is deleted."*

The old `/projects/{p}/processes/{name}` redirects here (Phase 2 §2B).

### 4.3 Jobs — `/projects/{p}/runtime/jobs`

**Primary:** **Add job**.

```
Jobs                                                       as of 14:02
Times are UTC. Jobs run on production only, never on previews.
After downtime, only the latest missed run is made up (looking back 25 hours).
                                                             [ Add job ]
─────────────────────────────────────────────────────────────────────────
cleanup    every day at 03:00     × FAILED 03:00 · exit 1 · 4s     Run now
           next run 03:00 tomorrow
─────────────────────────────────────────────────────────────────────────
digest     every hour             ● SUCCEEDED 14:00 · 12s          Run now
           next run 15:00
─────────────────────────────────────────────────────────────────────────
```

- **Row:**
  1. name (link)
  2. schedule in words
  3. last run: mark + word + time + detail (Phase 3 §9)
  4. next run
  5. **Run now** (secondary in rows; the page primary is Add job)
- **Paused:** *"Paused · no runs are scheduled."* + Resume. Job pause is immediate (Q-S4).
- **Running:** *"Running · {elapsed}"*. Pending and Skipped use the Phase 3 sentences.

**Job detail** — `/projects/{p}/runtime/jobs/{name}`:
- the definition
- **run history** as rows (time · state · duration · exit code), each run's output behind `<details>`
- Run now · Pause/Resume
- **Remove job** in a separated zone, with a `<details>` confirmation

---

## 5. Configuration — quieter by design

**Treatment (owner direction):**
- no state-coloured banners except the Q-S3 notice
- no contained production block
- neutral headings and forms
- the primary action on each sub-page is its form's own (Add / Save)

Configuration pages **never auto-refresh** (Q-S2).

### 5.1 Configuration index — `/projects/{p}/config`

```
Configuration
─────────────────────────────────────────────────────────────────────────
Environment    6 variables · 1 changed since production was built   →
Domains        2 domains · 1 waiting for DNS                         →
Storage        1 mount · included in the daily backup                →
Build          Next.js (detected) · 512 MB · keep 2 for rollback     →
Repository     ICOFCUCAM/balancevid · GitHub App · pushes deploy     →
─────────────────────────────────────────────────────────────────────────

┌─ Delete project ─────────────────────────────────────────────────────────┐
│ Deleting BalanceVid can't be undone.                                     │
│ Every deployment, worker and job stops now. Its domains stop serving.    │
│ Its deployment history is deleted.                                       │
│ Its stored files are kept on the server.                                 │
│ Type the project name to confirm:  [                    ]                │
│                                               [ Delete BalanceVid ]      │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Sub-page rows:** one line of state each (Phase 2 §2C); the row links to the page.
- **Delete zone:** at the bottom, separated, and red only in its button and boundary (Phase 4 §14).
  - The copy is Phase 3 §12 / Q-S5 exactly.
  - The typed name is checked on the server; a mismatch is S-FORM-ERROR.
  - Done: back to Projects with *"Deleted {name}. Its files are kept."*

**Route (locked by the owner):** `/projects/{p}/config`, the parent of the five sub-page URLs listed in Phase 2 §2B.

### 5.2 Sub-pages

All five share one pattern:
- title and one-sentence purpose
- the list of current items (rows)
- the add/edit form at the foot, with labels, descriptions (`aria-describedby`), control boundaries ≥3:1 and an explicit **Save** / **Add**
- errors next to the field and in a summary at the top of the form

| Page | Body (in order) | Primary |
|---|---|---|
| **Environment** `/config/environment` | Q-S3 notice when it applies: *"Changed since production was built — redeploy to apply"* + **Redeploy** (secondary, so the page keeps one primary) · variables grouped **All environments / Production only / Preview only**; the key in Mono, the value never shown, the updated time · the precedence note when a key exists in All and a specific scope · the read-only *"Provided by DeployPro"* list: `DEPLOYPRO_DEPLOYMENT`, `DEPLOYPRO_GIT_SHA`, `DEPLOYPRO_URL`, `DEPLOYPRO_ENV` · Remove per row (`<details>` confirmation: *"The value can't be recovered. Running deployments keep it until the next deploy."*) · the Add form (key, value as a password field, scope) | **Add variable** |
| **Domains** `/config/domains` | rows: host · mark + word (verified / waiting for DNS / pointing elsewhere / platform domain doesn't resolve) · primary / redirects to · **the exact DNS records** from the verifier for waiting states · Verify (secondary) · Remove (`<details>`: *"{host} stops serving immediately."*) · Add form | **Add domain** |
| **Storage** `/config/storage` | rows: mount path (Mono) · volume name · *"shared by production, its workers and jobs; previews never see it"* · backup inclusion · Remove (`<details>`: *"The next deployment won't mount it. The files stay on the server."*) · Add form. Size: absent (**[engine]**). | **Add mount** |
| **Build** `/config/build` | three groups under hairlines: **Build** (framework: *detected: X* or an override · root directory · install / build / start commands · port) · **Resources** (memory · CPU) · **Deployment behaviour** (instant rollback: keep N running · graceful shutdown time). Each group states when it takes effect: build fields and resources on the next deploy, graceful shutdown on the next replacement. | **Save** |
| **Repository** `/config/repository` | repository · production branch · how DeployPro reads it (GitHub App / public URL / deploy key) · how pushes arrive (GitHub App / webhook URL with *"If you added this webhook on GitHub, pushes deploy"* / none) · the deploy key (replace inside `<details>`: *"The current key stops working now…"*) · the engine's message verbatim when the app can't see the repository | **Link to GitHub** when linkable, otherwise **Save** (branch) |

---

## 6. System → Settings — `/system`

The page reads *"What is happening with DeployPro itself?"*

**In order:**
1. **GitHub App:** state (S-GITHUB-*), installations, **Install on GitHub** when created but not installed.
   - **Disconnect** is in a separated zone with a `<details>` confirmation: *"Pushes stop deploying. Imported projects stay but no longer deploy on push. Delete the app on GitHub too."*
2. **Installation:** version / branch, dashboard address, deploy domain.
3. **Installation settings, read-only:** alerts webhook (set or not; the value hidden), backup hour, image retention, and so on. Each says *"Changed in /opt/deploypro/.env"*.
4. **Checks:** `doctor`, test alert, manual backup. Absent until **[small]**.

**Primary:** depends on state. **Connect GitHub** when no app exists; otherwise none.

**Sign-in page:** layout unchanged (owner decision). It only inherits the Phase 4 tokens and fonts through `base.html`.

---

## 7. Validation of this layout against Phases 2–4 and the engine

| # | Item | Verdict | Note |
|---|---|---|---|
| V1 | `Deploy {branch}`, full label | Compatible | Phase 3 wording kept |
| V2 | Long-branch truncation | **Adjusted to locked Phase 2 §2I** | The owner's direction allowed "CSS truncation/ellipsis". §2I (locked) requires shortening **in the middle** and nothing hover-only. The layout uses a middle ellipsis (two spans), a complete accessible name, and the full branch printed in the identity line. |
| V3 | Projects "one obvious next action" | **Resolved by Phase 2 §2C** | The page's one primary action is **New project**. Each record's next action lives on its Overview; rows carry no buttons. |
| V4 | Projects "domain condition" | Compatible, with a limit | Shown as its own line. It doesn't change Needs attention, whose four conditions are locked in Phase 3 §6. |
| V5 | Overview item 7, configuration sections | **Resolved by Phase 2 §2C** | Links only; the Overview contains no configuration form. |
| V6 | Needs-attention placement | Added | Directly under production, as Level 1 state (Phase 2 §2D). The owner's list had no slot for it. |
| V7 | "Roll back to this" | **Adjusted to Phase 3** | Label **Roll back to #N**, target always named (Phase 4 C10). |
| V8 | Preview primary action | Compatible | Same as other ready deployments (Phase 3 §5.1; the engine's `promote()` accepts previews) |
| V9 | S-ROLLBACK "when" | Engine limit | Not recorded (`promoted_at` **[small]**). No time shown. |
| V10 | Live header vs Phase 3 §5.2 | **Phase 3 finding** | Today only the terminal transition updates the page. §3.3 specifies the in-place state event (web layer) and a truthful fallback. |
| V11 | Q-S2 on the deployment page | Compatible | The meta refresh is used only in `<noscript>`, never with a live log. |
| V12 | Runtime and Configuration index URLs | **Locked (owner)** | Phase 2 §2C pages without a Phase 2 §2B URL: `/projects/{p}/runtime`, `/projects/{p}/config` |
| V13 | Workers vs deployments | Compatible | No line, no outcome marks, no commit hierarchy on runtime pages |
| V14 | Cards | Compatible | Three contained surfaces only (production block, log, destructive zones) |
| V15 | Phase 4 semantics | Compatible | Production branch ≠ current production; current production is a marker; preview ends at DEPLOY; health inside DEPLOY; rollback a transition; one primary per view; no switch animation |
| V16 | Accessibility | Compatible | Landmarks, skip link, the line as `<ol>` with text states, `aria-current`, no hover-only content, confirmations without JavaScript, focus order preserved on mobile |
| V17 | Server-rendered / no build step | Compatible | Every page works without JavaScript. JavaScript remains progressive enhancement: the live log, the state event and the elapsed tick. |

**All items resolved.** V12 is locked by the owner: `/projects/{p}/runtime` and `/projects/{p}/config` are page-family routes, not menu headings. The five configuration sub-pages sit under `/projects/{p}/config/…`.

**Live deployment page, locked in order of preference:**
1. a state-change event over the existing SSE connection
2. without JavaScript: a timed refresh only while in progress
3. if (1) proves problematic: today's behaviour with an explicit "as of HH:MM"

**Phase boundary:**

| Phase | Covers |
|---|---|
| 2 | product and navigation doctrine |
| 3 | behaviour, state and action rules |
| 4 | the visual design system |
| 5 | page layouts and information hierarchy |
| 6 | implementation, pre-merge checks, and the remaining small engine/UI changes |

The P6 items below are implementation work, not design.

## 8. Constraints recorded for Phase 6 (found in the code; not design changes)

- **P6-1 · Splitting settings forms.** `POST /projects/{slug}/settings` treats an absent `root_directory`, `framework`, `build_command` or `start_command` as *clear it*. If the Repository page posted only `production_branch` to it, **the Build overrides would be wiped.** Build and Repository need their own handlers, or a handler that updates only the fields present.
- **P6-2 · Redirect targets.** Every form handler redirects to `/projects/{slug}` today. Each must return to the page its form is on, so `?ok`/`?err` appear there (Phase 3 §12).
- **P6-3 · Fields not yet in any form:** `install_command`, `port`, `cpu_shares`. The columns exist and the Build page lists them; adding them is a web change only.
- **P6-4 · Promote/rollback message.** Today it's *"Production now serves #N."* Phase 3 §7's *"Production is now #N ({commit}). #M is still available…"* replaces it.
- **P6-5 · Old URLs.** `/projects/{p}/processes/{name}` redirects to the Runtime detail pages. `/deployments/{id}` is unchanged.
- **P6-6 · The stream state event (§3.3).** Needs a regression test that the event is sent once per change and that `done` still ends the stream.
- **P6-7 · Carried from Phase 4:** the greyscale shape test before merge; the `pyproject.toml` package-data pattern for font subfolders.
