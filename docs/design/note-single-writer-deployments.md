# DeployPro — Note: Deployments that must not overlap

**Status:** **RECORDED 2026-09-30. A design constraint, not a change.** Nothing here is implemented. It records what DeployPro assumes today and where that assumption fails, so that a later phase can decide how to express it.
**Found through:** BalanceVid's Online TV. "Engine: not responding" led to the observation that older BalanceVid deployments stay running beside the current one and mount the same `/data` volume. BalanceVid requires exactly one playout writer per channel (`docs/DEPLOYMENT.md` in that repository). Whether the overlap caused the symptom is **not yet confirmed**. That investigation is BalanceVid's, and is kept apart from this note.

---

## 1. What DeployPro assumes today

DeployPro treats every application as **stateless enough to run twice**. Two mechanisms rely on this.

| Mechanism | Where | What overlaps |
|---|---|---|
| **Instant rollback** (`keep_warm`, default 2) | `engine/promote.reclaim` | The newest *N* superseded deployments keep running after a promotion, so a rollback is a router change and not a restart. Each keeps the volumes it was launched with, because production deployments mount the project's volumes (`engine/launch.py`). |
| **Zero-downtime handover** | `engine/promote.promote` | The new deployment is started and proven healthy *before* the router moves to it. The old one is then drained, with up to `stop_timeout_seconds` for in-flight requests. For that window both are running, both with the volumes mounted. |

For a stateless web app, both are exactly right: two copies can each answer requests. They are what make a deploy invisible and a rollback instant.

## 2. Where it fails

Some applications run a process that must be **the only one of its kind** against a piece of shared state. For example:
- a broadcast or playout encoder writing one channel's stream directory (BalanceVid)
- a queue consumer whose queue has no locking (a file queue on a shared volume)
- a scheduler that fires each job once, where two copies fire it twice
- an embedded database file (SQLite without WAL-safe access, and the like) opened by two processes

For these, a second running deployment is not a spare. It is a **second writer**. And the process runs *inside the web container* when the app starts all its roles from one entrypoint, as BalanceVid's `ROLE=all` does, so DeployPro can't see it.

**Two separate exposures, not one:**
1. **Kept-warm deployments.** These overlap for as long as they stay warm, possibly days. **Setting `keep_warm = 0` removes this one.** Rollback still works, from the kept image; it takes a start instead of a switch.
2. **The handover window.** This overlaps for as long as the new container takes to become healthy, plus the old one's drain. **`keep_warm = 0` does not remove it.** In that window, two playout engines write the same channel.

DeployPro's own workers (`processes.reconcile_workers`) follow production and are replaced on promotion. They have the same handover overlap, but no warm copies.

## 3. The distinction a later phase should make

```
Stateless (the default, today's behaviour)
  → the new deployment starts, becomes healthy, takes traffic; the old one drains
  → superseded deployments may stay warm for instant rollback

Single-writer (declared by the project)
  → the old deployment is STOPPED before the new one starts its writers
  → no warm copies: keep_warm is forced to 0 and the setting says why
  → a deploy has a short gap; the UI says so ("stops #N, then starts #M")
```

**Open questions, for the phase that takes this up:**
- **Where it is declared.** A project setting (Build → Deployment behaviour), a key in `deploypro.json`, or both. The repository knows best: BalanceVid could declare it next to `ROLE`.
- **Whole container or one role.** Stopping the old deployment first costs web downtime too, because BalanceVid's writer lives in the web container. An app split by role (`ROLE=web` as the web service, `ROLE=playout` as a DeployPro worker) could keep a zero-downtime web tier and stop-then-start only the writer. So the constraint may belong to a **worker**, not to the project.
- **Rollback.** A single-writer rollback is a stop and a start. The Phase 3 rollback copy ("instant") would need a variant.
- **Previews.** Previews never mount the project's volumes, so they can't be second writers of *that* state. They are unaffected.

## 4. What to do today, without code

For a project with a single writer inside its web container:
- **Build → Instant rollback: keep 0 running.** This removes the long-lived duplicates at the next promotion. Nothing is stopped until then: redeploy, or promote, to apply it.
- **Accept the handover window,** or split the writer into its own DeployPro worker so it can be reasoned about separately.
