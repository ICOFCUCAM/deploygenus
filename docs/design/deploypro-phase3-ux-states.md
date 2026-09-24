# DeployPro: Phase 3, UX States (dashboard)

**Status: LOCKED (2026-09-24).** Design specification only. No code, CSS or templates change.
**Code baseline:** `ICOFCUCAM/deploygenus` @ `7cc2130` (engine frozen).
**Builds on:** `deploypro-phase2-final.md` (**LOCKED 2026-09-24**): structure, page ownership, hierarchy, personality, status principle. DeployPro has its own identity. Visual treatment is **not** decided here (Phase 4). "Mark" means the state mark whose shape, word and colour Phase 4 defines.
**Freeze:** deployment functionality and the current deployment page don't change before Phase 6. The live page is source material.

**Scope:** the private dashboard only. The public site's states are trivial (static pages) and are left to Phase 5.

---

## 0. Rules every state obeys

1. **A state is shown only if the engine records it, or a stated rule derives it from recorded data.** (INV-00, Appendix C.) Every state below names its **source**.
2. **Each tag says where the data comes from:**
   - **[now]**: the engine has the data today
   - **[small]**: needs a contained engine change after the freeze. Until then the state is **not shown**, and the fallback is named.
   - **[engine]**: needs new engine work. Not shown, with a fallback named.
3. **A missing capability is hidden. A failure is always stated** (D-15). An empty list must never be the way a failure appears.
4. **State → identity → action → (operational) → technical detail** is the order within every state (Phase 2 · 2D).
8. **A deployment has three independent dimensions, and they are never collapsed into one badge** (owner, 2026-09-24):
   - **deployment state** (queued · building · deploying · ready · failed · cancelled)
   - **production relationship** (current production or not)
   - **runtime state** of a ready deployment (running · stopped · removed)

   Every deployment view shows each dimension that applies, as its own signal. Examples: *ready + production + running*; *ready + not production + stopped* (still a valid rollback target); *ready + production + stopped* = production missing (**Needs attention**).
5. **No spinner without a number** (D-13). Every in-progress state shows elapsed time or a count.
6. **Server-rendered.** A page is a snapshot at the moment it was loaded. Live change exists only where the engine already streams it (the build log over SSE). Everywhere else the page must **say how old it is** (§11).
7. **Copy.** Product words from Phase 2 (2D terminology). The engine's stored error text is shown **verbatim** (D-13 "document visible"); DeployPro's framing sentence goes around it, never in place of it.

---

## 1. State inventory (all pages)

| Code | State | Source | Tag |
|---|---|---|---|
| S-EMPTY-PROJECTS | no projects | no project rows | now |
| S-EMPTY-DEPLOYS | project never deployed | no deployments for the project | now |
| S-EMPTY-WORKERS / S-EMPTY-JOBS | none defined | no process rows of that type | now |
| S-EMPTY-DOMAINS | no custom domain | no domain rows | now |
| S-EMPTY-ENV / S-EMPTY-STORAGE | none defined | no rows | now |
| S-EMPTY-RUNS | job never ran | no job_runs | now |
| S-QUEUED | waiting for the worker | status queued | now |
| S-BUILDING | building | status building, started_at | now |
| S-DEPLOYING | container started, health check under way | status deploying, built_at | now |
| S-READY-PROD | serving production | `production_deployment_id == id` and ready | now |
| S-READY-RUNNING | ready, container running | ready and container_id set | now |
| S-READY-STOPPED | ready, container stopped | ready and container_id null | now |
| S-READY-REMOVED | ready, image gone | image missing | small (fallback: S-READY-STOPPED, with the error shown on action) |
| S-FAILED | failed | status failed, error, finished_at | now |
| S-CANCELLED | cancelled | status cancelled | now |
| S-PROD-MISSING | production's container is gone | production deployment has container_id null | now |
| S-DEGRADED | **Needs attention** (derived) | §6 rule | now (worker counts: small) |
| S-NOT-ANSWERING | site not answering HTTP | monitor results aren't stored | engine. **Never shown** until stored. |
| S-ROLLBACK | production moved to an older deployment | trigger rollback, pointer changed | now |
| S-WORKER-RUNNING / BELOW / PAUSED / FINISHING / WAITING | worker realities | process row + Docker | now / small (§8) |
| S-RUN-* | job run: pending, running, succeeded, failed, timed out, skipped | job_runs.status | now |
| S-DOMAIN-VERIFIED / WAITING / MISPOINTED | domain | verified_at + verify result | now |
| S-GITHUB-* | none, created but not installed, installed, unreachable, repository not visible | github_app, installations, API errors | now |
| S-FORM-ERROR | a submitted form was refused | the engine's error message (redirect `?err=`) | now |
| S-ACTION-DONE | an action succeeded | redirect `?ok=` | now |
| S-STALE | the page is older than the world | page render time | now (rendering only) |
| S-SIGNED-OUT | no or expired session | session cookie | now |
| S-DEP-UNREACHABLE | Docker or GitHub unreachable while rendering | adapter error | now (must be surfaced, §10) |

---

## 2. Projects (home)

### S-EMPTY-PROJECTS
- **Shows:** one sentence: *"Nothing is deployed here yet."* Then one action, chosen by GitHub state:
  - GitHub not connected → **Connect GitHub** (secondary link: "or deploy from a Git URL")
  - Connected → **Import a repository** (goes to New project)
- **Doesn't show:** activity, filters, sample projects.

### Populated: one record per project, ordered by *needs attention first, then most recently deployed*
Each record carries, in order:
1. **Project state** (derived, §6): *Healthy* / *Needs attention* / *Not deployed*. *Down* is not available (S-NOT-ANSWERING is engine).
2. **Name and address**:
   - the verified primary domain
   - else *"No permanent address — add a domain"* (Phase 2: production has no stable URL without a domain)
3. **Production:** #N · commit · branch · relative time. If never deployed: *"Not deployed yet"*.
4. **Latest, only if different from production and not ready:**
   - *#N building · 2m*, or
   - *#N failed · 20m ago* + the first line of the error
5. **Workers:** *running/wanted* **[small]**. Fallback today: the count of defined workers, with no running claim ("2 workers").
6. **Jobs:** the worst recent run state (*1 failing* / *all succeeding* / *none*).

**Activity** (secondary, below the projects, **[now]**): the last 10 events across projects, derived from deployments (queued/ready/failed/rollback) and job runs (failed/timed out). *"Worker restarted"* isn't recorded, so it's absent.

---

## 3. Overview (project)

| State | Condition | Shows (in hierarchy order) | Primary action |
|---|---|---|---|
| S-EMPTY-DEPLOYS | no deployments | *"Nothing is deployed yet."* · repository · production branch | **Deploy {branch}** |
| First deploy running | only deployment is queued/building/deploying | the deployment's state + elapsed time → link to its live page | **View deployment** (Deploy is disabled while one is queued for this branch: see §12 Q-S1) |
| First deploy failed | only deployment failed | *"The first deployment failed."* · the step · the stored error (first line) · *"Nothing was serving, so nothing changed."* | **View deployment** / **Deploy again** |
| S-READY-PROD | production is serving | **Serving** · address · #N commit message · since {ready_at relative} · rollback target and its speed | **Deploy {branch}** |
| S-PROD-MISSING | production's container is gone | **Production is down: its container is gone.** · #N · when last seen isn't recorded (don't invent it) | **Roll back to #M** (if a ready candidate exists), else **Redeploy #N** |
| Newer deploy in progress | production ready, a newer production-branch deployment in progress | production block as S-READY-PROD, plus a line: *"#N+1 is building · 2m — production switches when it passes its health check."* | **View #N+1** |
| Newer deploy failed | production ready, latest production-branch deployment failed | production block, plus: *"#N+1 failed at {step}. Production is still #N."* + error first line | **View #N+1** |
| S-ROLLBACK (recent) | production's deployment has trigger rollback, or was promoted after a newer one | *"Rolled back to #N"* · from which deployment (the newer ready deployment that isn't production) · when | **Deploy {branch}** |

**The rollback control (in S-READY-PROD):**
- The target is the newest *older* ready deployment of the production branch.
- Speed: *instant* if S-READY-RUNNING; *restarts in a few seconds* if S-READY-STOPPED; **unavailable** if S-READY-REMOVED (**[small]**; today the refusal happens on click and is shown as S-FORM-ERROR).
- No candidate: the control is absent, not disabled.

**Runtime summary:** web · workers · jobs, each a line with its own state (§8, §9), linking to Runtime.

---

## 4. Deployments (list)

**Row states:** outcome mark + word · kind (*Production* / *Preview*) · the current-production marker on one row only · #N · commit message · branch · relative time · duration (finished) or elapsed (in progress) · container reality for ready rows (*running* / *stopped*).

| State | Shows |
|---|---|
| S-EMPTY-DEPLOYS | *"No deployments yet."* + **Deploy {branch}** |
| Filter matches nothing | *"No {preview/production} deployments"* or *"None match '{search}'"* + clear filter. Distinct from empty. Filters are **[small]**. |
| More than one page | older/newer links. Pagination is **[small]**; today the newest 25 only, and the page says *"Showing the latest 25."* |
| Commit message missing | Dashboard/CLI/import deploys have none today (the engine discards it at clone, **[small]**). Show the branch and short commit only, never a blank or "—". |

**Kind caveat [small]:** kind is derived from the *current* production branch. If the branch was renamed, older rows can change kind. Until kind is stored, show the kind as derived and don't claim history. No UI state is needed, only awareness in Phase 5.

---

## 5. Deployment (detail)

### 5.1 Lifecycle slots (the deployment line, Phase 2 · 2G)
`Queued (created_at) → Building (started_at…built_at) → Deploying (built_at…ready_at) → Ready (ready_at)` + the current-production marker.

| State | Slots | Headline (state + identity) | Body | Actions |
|---|---|---|---|---|
| S-QUEUED | Queued active | **Queued** · #N · commit · branch | *"Waiting for the build worker · {elapsed}."* One build runs at a time on this server: if another is building, name it (**[now]**: the oldest building deployment). Queue position is **[small]**. | **Cancel** |
| S-BUILDING | Queued ✓, Building active | **Building** · elapsed since started_at | the live build log (SSE), auto-following | none (cancelling a running build is **[engine]**) |
| S-DEPLOYING | Building ✓ (duration), Deploying active | **Deploying** · elapsed since built_at | *"Starting the container and checking it answers."* + the live log (container start and "healthy after…" lines appear here) | none |
| S-READY-PROD | all ✓ + marker | **Ready · Current production** | address (link) · durations · log (collapsed to the end) · details | **Redeploy** |
| S-READY-RUNNING (not production) | all ✓ | **Ready · Preview** or **Ready · Production branch, not current** | address · *"Running — making it production is instant."* | **Make production** (newer than production) / **Roll back to #N** (older) · **Redeploy** |
| S-READY-STOPPED | all ✓ | **Ready · Stopped** | *"Stopped to free memory. Making it production restarts it from its image in a few seconds."* | same as running |
| S-READY-REMOVED [small] | all ✓ | **Ready · Removed** | *"Its image was cleaned up. It can't be restarted — redeploy this commit instead."* | **Redeploy** only. Fallback today: shown as stopped, and the refusal appears as S-FORM-ERROR on click. |
| S-FAILED | slots up to the failed one ✓, the failed one ✕, later slots inactive | **Failed at {step}** · #N | the stored error **verbatim**, under the failed slot · *"Production was not touched."* (production-branch deployments) or *"This was a preview; production was not involved."* · log opened at the end (for a health failure, the container's last output is there, marked as the app's own output) | **Redeploy** |
| S-CANCELLED | Queued ✓, then ended | **Cancelled** · when | *"Cancelled before it started building."* | **Redeploy** |

**Which step failed (derived, [now]):**
- no started_at → Queued (the worker never picked it up, or it was reclaimed as abandoned)
- started_at but no built_at → **Building**
- built_at but no ready_at → **Deploying**

**Abandoned deployments:** reclaimed deployments fail with an engine-written error. Show it verbatim like any other.

**Preview-specific line (all preview states):** *"Preview: its own address and preview variables; no production storage, workers or jobs."*

### 5.2 Transitions while the page is open
The build log already streams, and the page reloads itself when the deployment finishes (existing behaviour). So S-QUEUED → S-BUILDING → S-DEPLOYING → terminal happen without user action, provided JavaScript is on. Without JavaScript: §11.

---

## 6. Derived state: *Needs attention* (never stored)

**A project needs attention if any of these is true:**

| Condition | Source | Tag | What the user is told |
|---|---|---|---|
| Production container missing | production deployment, container_id null | now | *"Production is down: its container is gone."* |
| Workers below wanted | Docker count < sum of replicas (enabled workers) | small | *"render: 0 of 1 running."* |
| Last job run failed or timed out | latest job_run per job | now | *"cleanup failed at 03:00."* |
| Latest deployment failed, production fine | newest deployment failed | now | *"#13 failed. Production is still #12."* |

- **Several true at once:** list all of them, most serious first, in this order: production missing → workers below wanted → job failed → deployment failed. This follows D-07's "how unrecoverable" ordering, applied to impact.
- **Clears when:** the underlying condition no longer holds on the next page load. There's no acknowledge/dismiss state, since that would need storage (INV-00).
- **Not a condition, because it isn't recorded:** *site not answering* (S-NOT-ANSWERING, **[engine]**).

---

## 7. Rollback, as a moment

1. **Before (on the button):** target #N, and speed (instant / a few seconds / unavailable).
2. **During:** the action is a form POST that runs the switch before responding, so there's no in-between screen. The page returns when it's done.
3. **After (S-ACTION-DONE):** on the page you return to: *"Production is now #N ({commit}). #M is still available: roll forward any time."* Name #M only if it's still ready.
4. **If it fails** (S-FORM-ERROR): the engine's reason verbatim (e.g. *"The image for deployment #N is no longer on this host, so it cannot be started without rebuilding. Redeploy the commit instead."*) + *"Production is unchanged."*
5. **History:** there's no new deployment row, because a rollback moves the pointer. The Overview shows *"Rolled back to #N"* while that deployment is production and a newer ready deployment exists (derived, [now]). A durable rollback history needs **[small]** (recording promotions).

---

## 8. Runtime: workers

| State | Source | Tag | Shows | Action |
|---|---|---|---|---|
| S-EMPTY-WORKERS | no workers | now | *"No workers. A worker is a long-running process from the same image as the site — a queue consumer, a renderer."* | **Add worker** |
| Waiting for deploy | defined, never started (no production, or added since the last promotion) | now (no production) / small (added-since) | *"Starts on the next deploy."* | none; **Deploy {branch}** as a link |
| Running n/n | Docker count = replicas | small | **Running · n/n** · follows production #N | **Pause** |
| Below wanted | count < replicas | small | **Below wanted · k/n**. The reason isn't recorded, so don't guess. Point to the server command that shows it until runtime logs exist. | **Pause** |
| Pause requested (still running) | enabled = false, but its container is still running (the engine applies it only at the next deploy) | now | **Pausing: stops at the next deploy** · *"It keeps running until the next deployment."* | **Resume** · **Deploy {branch}** (to apply now) |
| Paused | enabled = false and no container | now (container check: small) | **Paused** · *"Not running. Resuming starts it at the next deploy."* | **Resume** |
| Finishing work | a `.draining.<deadline>` container exists | small | *"A previous worker is finishing its work · up to {m:ss} left."* | none |
| No production | project not deployed | now | *"Workers run the production deployment. Nothing is in production yet."* | none |

**Fallback until [small] lands:** show the definition (name, command, replicas, paused or not) and *"follows production #N"*. **Never a running count.**

---

## 9. Runtime: jobs

| State | Source | Shows | Action |
|---|---|---|---|
| S-EMPTY-JOBS | none | *"No scheduled jobs. A job runs a command from the production image on a schedule, in UTC."* | **Add job** |
| Never run | job exists, no runs | schedule in words · *"First run {next_due}."* | **Run now** |
| Last run succeeded | latest run | ✓ *Succeeded* · when · duration · next run | **Run now** |
| Last run failed | latest run failed | ✕ *Failed* · exit code · detail · output behind a disclosure · next run | **Run now** |
| Last run timed out | timed_out | *Timed out after {timeout}* · output · next run | **Run now** |
| Running | status running | *Running · {elapsed}* | none |
| Pending | status pending | *Waiting to start*. Up to 3 jobs run at once, so name the reason only if derivable. | none |
| Skipped | status skipped | *Skipped: nothing was in production.* | none |
| Paused | enabled = false | *Paused · no runs are scheduled.* | **Resume** |

**Rule statements shown on the Jobs page:**
- times are UTC
- jobs run on production only, never on previews
- after downtime, only the latest missed run is made up, looking back up to 25 hours

---

## 10. Configuration states

| Page | States |
|---|---|
| **Environment** | empty · list · **pending redeploy** (a variable changed after the production deployment was built. **[now]**: compare `updated_at` with production's `started_at`) → *"Changes apply on the next deployment"* + **Redeploy production** · **precedence note** when a key exists in both All and a specific scope: *"The Production only value is used in production"* (true since the fix) |
| **Domains** | empty · **verified + primary** · **verified, redirecting** · **verified, not yet serving** (no production) · **waiting for DNS** (with the exact records to add, from the verifier: *"{host} does not resolve. Add a CNAME to {deploy domain}, or an A record to {ip}."*) · **pointing elsewhere** (*"{host} resolves to {x}, but this platform is at {y}"*) · **platform domain doesn't resolve** (the verifier's own message) |
| **Storage** | empty · mounts (path, name, *"shared by production, its workers and jobs; previews never see it"*, *"included in the daily backup"* or *"backups are off"*) · after removal: *"The files are kept."* Size is **[engine]** (hidden). |
| **Build** | detected (no overrides) · overridden (which fields) · the last detected framework (from production's deployment, [now]) |
| **Repository** | **linked to the GitHub App** · **public URL, no push deploys** · **webhook configured** (can't be verified: DeployPro only knows a secret exists, not that GitHub uses it, so say *"If you added this webhook on GitHub, pushes deploy"*) · **deploy key made** · **app installed but can't see this repository** (the engine's message, verbatim) |
| **Build** (resources + deployment behaviour, locked) | memory · CPU · instant rollback: keep N running · graceful shutdown time. Each states **when it takes effect**: build fields and resources on the next deploy; graceful shutdown time on the next replacement. |
| **Configuration → bottom** | **Delete project** (§12) |
| **System → Settings** | GitHub App with **Disconnect** (§12) · installation settings **read-only**, each with where it's changed (`/opt/deploypro/.env`) · checks when exposed (**[small]**) |

---

## 11. Freshness: the server-rendered constraint (S-STALE)

- Every page states **when it was rendered** (*"as of 14:02"*), except where it's live (the build log).
- **Where live updating exists today:** the deployment page (SSE log + reload on finish).
- **Where it doesn't:** everything else. **Decision Q-S2:** automatic refresh on in-progress states only (a meta refresh, works without JavaScript), or a manual *Refresh* link. Recommendation: meta refresh on Overview and Projects **only while something is in progress** (queued/building/deploying, a job running, a worker finishing), and never on configuration pages (it would discard what's being typed).

---

## 12. Forms, actions, destructive actions

### S-FORM-ERROR / S-ACTION-DONE (existing mechanism: redirect with `?err=` / `?ok=`)
- The message appears **on the page the form was on** (Phase 2: today everything returns to the project page), next to the form, and keeps what was typed where possible. Retaining typed values across the redirect needs **[small]**.
- Engine messages are shown verbatim. They're already written for people, for example:
  - *"A mount path must be absolute, e.g. /data"*
  - *"{host} is already attached to a project"*
  - *"This project already has a process called 'render'"*

### Destructive actions
Each has three states: **offered → confirming → done**. Confirmation is the no-JavaScript pattern: a confirmation step (a page or section) that states the consequence, with the final button on it. It replaces today's inconsistent `confirm()` pop-ups.

| Action | Reversible | The confirmation states | Done state |
|---|---|---|---|
| Remove variable | **no** | *"The value can't be recovered. Running deployments keep it until the next deploy."* | removed · *"Redeploy to apply."* |
| Remove domain | yes | *"{host} stops serving immediately."* | removed |
| Remove mount | yes (data kept) | *"The next deployment won't mount it. The files stay on the server."* | removed · *"Files kept."* |
| Pause worker / job | yes | none (reversible, no confirmation) | paused |
| Remove worker / job | **no** (run history deleted) | *"Its run history is deleted."* | removed |
| Delete project | **no** (volumes kept) | *"Every deployment, worker and job stops now. Its domains stop serving. Its stored files are kept on the server."* + type the project name | back to Projects · *"Deleted {name}. Its files are kept."* |
| Disconnect GitHub | **no** | *"Pushes stop deploying. Imported projects stay but no longer deploy on push. Delete the app on GitHub too."* | disconnected |
| Replace deploy key | **no** | *"The current key stops working now. Add the new one to GitHub before the next deploy."* | new key shown |

---

## 13. Session and dependency states

| State | Shows |
|---|---|
| S-SIGNED-OUT / session expired | sign-in page, one field, returns to the page asked for ([now]) |
| Wrong token | *"That token is not valid."* ([now]) |
| GitHub unreachable (New project, System → GitHub) | *"Couldn't reach GitHub: {reason}."* The URL path stays available. **Never an empty list.** |
| GitHub app created, not installed | *"The app exists but isn't installed."* + **Install on GitHub** |
| Docker unreachable while rendering runtime | *"Couldn't read running containers: {reason}."* Runtime counts are hidden **with this message**, not silently. |
| Engine refuses an action | S-FORM-ERROR with its reason |

---

## 14. Decisions (locked 2026-09-24)

- **Q-S1 · Deploy while a deploy is waiting or building:** the button stays available. When one is already queued or building for that branch, it says so: *"#N is already building — this will wait and build again after it."* It's never silently disabled.
- **Q-S2 · Auto-refresh:** Overview and Projects refresh automatically (a meta refresh; no JavaScript needed) **only while something is in progress** (a deployment queued/building/deploying, a job running, a worker finishing its work). Configuration pages never auto-refresh. Every page shows *"as of HH:MM"*.
- **Q-S3 · Variables changed since production was built:** show *"Changed since production was built — redeploy to apply"* + **Redeploy**. Derived from `updated_at` vs production's build start. Accepted: it also shows after a change that was later reverted.
- **Q-S4 · Worker pause/resume wording:** checked in the code (`web/routes.toggle_process` only flips `enabled`; workers are reconciled only on promotion):
  - **Workers:** pause and resume take effect **at the next deploy**. A paused worker keeps running until then. Copy: *"Pausing: stops at the next deploy"* / *"Resumes at the next deploy"* (§8).
  - **Jobs:** pause and resume take effect **immediately** (the scheduler only reads enabled jobs, within about 20 seconds). Copy: *"Paused · no runs are scheduled."*
  - **Post-freeze engine item [small]:** apply worker pause/resume immediately (reconcile on toggle). Until then the copy above is the honest description, and today's *"render paused."* message is misleading.
- **Q-S5 · Delete project:** confirmed by **typing the project name**. The confirmation states: everything stops now, it can't be undone, history is deleted, stored files are kept on the server.
- **Q-S6 · Activity feed:** the **last 10 events across all projects**, on the Projects home only: deploys that went live, failures, rollbacks, job failures. Each project's own recent activity is on its Overview.

## 15. Hand-off to Phase 4 (design language)

Phase 4 must give every state above a treatment within the Phase 2 constraints:
- shape + word + colour; greyscale-safe; a static form under reduced motion
- one set of marks for: outcome, kind, current production, container reality, worker, job run, domain, derived project state (the full list is Phase 2 · 2H), with **all deployment outcomes distinguishable by shape alone**
- an explicit treatment for **"in progress"**, and for **"hidden because not yet available"**, which is no treatment at all
- the three deployment dimensions (rule 8) as three separate signals
