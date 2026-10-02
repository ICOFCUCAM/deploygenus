# DeployPro — Proposal: Production first, newer replaces older, previews optional

**Status:** **APPROVED 2026-10-01 ("start") and implemented** with the recommended answer to each decision (§5).
**Builds on (locked, not reopened):**
- `deploypro-phase3-ux-states.md`: the deployment states and the cancelled state's copy
- `deploypro-phase5-page-layouts.md` §5.2: the Build page's *Deployment behaviour* group, and the Repository page's "how pushes arrive" line

**Why:** BalanceVid's assistant pushes to its `claude/…` branch several times an hour and merges each change into `main` through a pull request.
- Every change is therefore built twice: once as a preview, once as production. On a 2-CPU server a build is 4–8 minutes of full CPU.
- Builds run one at a time, oldest first. On 1 October the production build #225 (the merge of PR #42) waited behind the preview #224.
- Previews keep running after they are built, each holding a full copy of the app in memory on a 4 GB server that was already using swap.

**Governing rule (owner):** *Every page makes the current system state and the next useful action obvious.* Here: what goes live should not wait behind what nobody asked for.

---

## 1. Production goes first

**Rule:** when the worker picks the next build, a queued deployment of a project's **production branch** comes before any queued preview. Within each group it is oldest first, as before.

- A build already running is **never interrupted**: stopping it halfway wastes the minutes already spent and leaves nothing.
- A rollback or redeploy of the production branch counts as production.
- Previews still run, in order, whenever no production build is waiting. On a host where production is pushed constantly, previews wait. That is the point.

## 2. Newer replaces older

**Rule:** when a new build is queued for a branch, any **still queued** build of the same project and branch is cancelled. A build that has already started is left to finish.

- Applies to builds of a branch's head: a push, or **Deploy {branch}**.
- Does **not** apply to a **redeploy** or a **rollback**. Those name a specific commit on purpose, and a newer commit is not a replacement for them.
- The cancelled deployment says why, on its page and in its history (Phase 3's cancelled state, with the reason in place of the generic sentence):
  *"Replaced by #226, a newer commit on main, before it started building."*

## 3. Previews can be switched off per project

**Where:** Build page → *Deployment behaviour*, beside instant rollback and the graceful shutdown time.

```
Deployment behaviour
─────────────────────────────────────────────────────────────────────────
[x] Deploy previews
    A push to another branch builds a preview with its own address.
    Off: only pushes to main deploy. Takes effect on the next push.
```

- **Default: on.** Every existing project keeps today's behaviour until its owner changes it.
- **Off:** a push to any branch other than the production branch is acknowledged and ignored. The webhook answers *"previews are off for this project"*, so GitHub's delivery log says why nothing was built.
- **Deploying another branch by hand still works:** `deploypro deploy <project> --ref <branch>`. Switching previews off stops the automatic builds, not the owner.
- **Previews already running stay** until the next production deploy stops them, as today.
- **The Repository page's "how pushes arrive" line** follows the setting:
  - on: *"A push to `main` goes live; a push to any other branch deploys a preview."*
  - off: *"A push to `main` goes live; pushes to other branches are ignored (previews are off)."*

---

## 4. What this does for BalanceVid

With previews off, each change is built **once**, when it is merged into `main`, and nothing queues ahead of it. That halves the CPU spent on builds and removes the preview containers from memory. Previews on or off, production can no longer wait behind a preview, and a burst of pushes builds only the newest commit.

## 5. Decisions (recommended answers taken)

- **D1 · Interrupting a running preview for production:** no. The build is half done, and interrupting it wastes those minutes.
- **D2 · Replacing queued redeploys and rollbacks:** no. They name a commit on purpose.
- **D3 · Previews default:** on for every project, existing and new. It's a choice the owner makes, not one DeployPro makes for them.
- **D4 · Stopping running previews when switched off:** no. They stop at the next production deploy, as now. A switch that stops containers is a surprise.

## 6. Recorded, not changed

- **Warm deployments include previews.** Instant rollback keeps the newest *N* ready deployments running, and previews count among them. So a run of previews can push the previous production build out of the warm set, and previews accumulate between production deploys (BalanceVid had 8 containers on 30 September).
- **Better:** count only production deployments as warm, and stop a preview when a newer preview of the same branch is ready. That changes what "instant rollback" keeps, so it is left for a later decision.
