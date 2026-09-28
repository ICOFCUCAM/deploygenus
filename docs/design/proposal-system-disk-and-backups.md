# DeployPro — Proposal: Disk and backups on the System page

**Status:** **PROPOSED 2026-09-28. Not locked, not implemented.** For the owner's decision (§6).
**Builds on (locked, not reopened):**
- `deploypro-phase5-page-layouts.md` §6: System → Settings. Item 4 already reserves *"Checks: `doctor`, test alert, manual backup. Absent until **[small]**."* This proposal fills that slot. It does not add a page, a route or a navigation item.
- `deploypro-phase3-ux-states.md`: §12 (form messages), Q-S2 (auto-refresh)
- `deploypro-phase4-design-system.md`: marks, tones, one primary per view

**Why now:** in September 2026 the server's disk filled up (the build cache was uncapped, and backups sat on the same disk). Postgres stopped, the worker stopped, and every deploy after failed with a database error. Recovery took terminal work.
- Since then, the clean-up is capped and hourly, and a build checks for room before it starts.
- None of that is visible, though. The owner still has to SSH in to learn how full the disk is, when the last backup ran, or to run either by hand.

**Governing rule (owner):** *Structure before decoration. Every page makes the current system state and the next useful action obvious.*

---

## 1. What the section answers

1. **Is the server running out of space?** Where, and how close to trouble?
2. **Is there a recent backup?** Where is it kept?
3. **Will I hear about it if something breaks?** (alerts)
4. **Can I act without a terminal?** Clean up now · Back up now · Send test alert.

---

## 2. Layout (desktop)

The layout below shows a normal state.

```
Disk and backups
─────────────────────────────────────────────────────────────────────────
Server disk      20% used · 30.1 GB free of 38 GB
Image storage    44% used · 32.4 GB free of 59 GB
Build cache      3.1 GB · capped at 8 GB
Last clean-up    16:24 · 2 images and 1.8 GB of cache removed · hourly
                                                      [ Clean up now ]
─────────────────────────────────────────────────────────────────────────
Last backup      Today 03:00 · 1.2 GB · 7 kept
Next backup      Tomorrow 03:00 UTC
Kept on          This server only (/var/backups/deploypro)
                 A backup here is lost with the server. Copy it off →
                                                      [ Back up now ]
─────────────────────────────────────────────────────────────────────────
Alerts           ▲ OFF · Nothing will tell you when a deploy or the disk fails.
                 Set DEPLOYPRO_ALERT_WEBHOOK_URL in /opt/deploypro/.env →
─────────────────────────────────────────────────────────────────────────
```

When alerts are on, the alerts row reads:

```
Alerts           On · Discord                          [ Send test alert ]
```

- **Rows:** use the `facts` pattern the page already uses (label, then value). Each group is separated by a hairline. There is no card: Phase 5 V14 allows three contained surfaces, and this section isn't one of them.
- **Buttons:** all three are secondary, and each sits under the group it acts on. By default the page's primary action stays as Phase 5 §6 defines it. See D3 for the one exception proposed.
- **Copy it off →:** links to the README section *Backups → Copy backups off the machine*. DeployPro cannot set that up itself (§5).
- **Two disk rows:** these appear only when the image storage is on a different disk from the server disk. On a single-disk server they collapse into one row, *Disk*. This server has had two disks since 28 September (the Hetzner Volume).

### 2.1 Mobile
- There is no layout change beyond the frame's.
- Rows stack label over value, as `facts` already does.
- Buttons go full width under their group.

---

## 3. States, marks and copy

Normal is quiet: **no mark** when all is well. A mark appears only when something needs attention. This follows Phase 4's rule that colour is reserved for state that asks for something.

| Row | Condition | Mark | Copy |
|---|---|---|---|
| Disk / Image storage | below `DEPLOYPRO_DISK_ALERT_PERCENT` (90) | none | `44% used · 32.4 GB free of 59 GB` |
| | at or above 90% | caution | `▲ 91% used · 3.4 GB free of 38 GB · Old images and cache are cleared hourly. If it stays this full, free space on the server.` |
| | free below `DEPLOYPRO_MIN_FREE_GB` (5) | failed | `× 97% used · 1.1 GB free · Builds are refused until there is 5 GB free.` Uses the same number and wording as the pre-build guard. |
| | can't be measured | caution | `▲ Can't read this disk.` |
| Build cache | always | none | `3.1 GB · capped at 8 GB`. It is never over the cap for longer than an hour, so this row carries no alarm. |
| Last clean-up | ran | none | `16:24 · {what was removed} · hourly`. When nothing was removed: `16:24 · nothing to remove · hourly`. |
| | running (requested from here) | building | `● Cleaning up… started 17:40` and the button is hidden. |
| | last run failed | failed | `× 16:24 · failed: {engine message verbatim}` |
| Last backup | taken in the last 26 h | none | `Today 03:00 · 1.2 GB · 7 kept` |
| | none, or older than 26 h | caution | `▲ None yet` / `▲ Yesterday 03:00 — today's didn't run` |
| | running | building | `● Backing up… started 17:40` and the button is hidden. |
| | last attempt failed | failed | `× Today 03:00 failed: {engine message verbatim}` |
| | backups turned off (`DEPLOYPRO_BACKUP_DIR` empty) | caution | `▲ Off · Set DEPLOYPRO_BACKUP_DIR in /opt/deploypro/.env`. The Back up now button is absent. |
| Kept on | always | none | `This server only ({path})` plus the one-line warning. The warning is permanent: DeployPro cannot see whether the owner copies backups off, so it cannot claim they are safe. |
| Alerts | off | caution | as in the wireframe |
| | on | none | `On · {Discord / Slack / webhook}`, detected from the URL host. The URL itself is never shown (Phase 5 §6: *"the value hidden"*). |

**Why 26 h:** a daily backup at 03:00 that took up to two hours is still "recent" at 04:59 the next day.

---

## 4. Actions

All three are ordinary `POST` forms that redirect back to `/system` with `?ok=` or `?err=` (Phase 3 §12). They work without JavaScript.

| Action | What it does | Confirmation | Message on return |
|---|---|---|---|
| **Clean up now** | Runs the same clean-up the worker runs every hour. It removes only build cache over the cap, unused images beyond rollback retention, leftover build folders, and logs past retention. It never touches production, rollback images, running containers, volumes or backups. | None. Nothing it removes is needed. | *"Clean-up started. This page shows what it removed when it finishes."* |
| **Back up now** | Takes a backup exactly as the daily one does. The oldest is dropped beyond `DEPLOYPRO_BACKUP_KEEP`. | None. | *"Backup started. It takes a few minutes; this page shows it when it's done."* |
| **Send test alert** | Sends the same message as `deploypro test-alert`. | None. | Delivered: *"Test alert sent. If it didn't arrive, check the webhook URL."* Failed: the engine's message verbatim. |

- **Twice in a row:** while a clean-up or backup is running, its button is replaced by the running state (§3). A second request is not queued. A `POST` that arrives anyway returns *"A backup is already running."*
- **Refresh:** while a clean-up or backup started here is running, `/system` refreshes itself (meta refresh, 10 s), and stops when it finishes. Otherwise it never refreshes. This extends Q-S2 to one more page; see decision D2.

---

## 5. Deliberately not on the page

| Not here | Why |
|---|---|
| Editing `.env` settings | The settings are read by the running containers at start. Changing one needs a restart, and a dashboard that restarts itself can't report whether it came back. They stay read-only (Phase 5 §6 item 3). |
| Installing DeployPro updates | The update replaces the containers the dashboard runs in. It stays `git pull` + `bash scripts/install.sh` in a terminal. |
| Restoring a backup, deleting backups | These are destructive and rare, and a mistake can't be undone. They stay on the command line (`deploypro restore-volume`). |
| Copying backups off the server | The storage and its credentials are outside DeployPro. The page links to the instructions. |
| `docker volume prune` or anything that removes volumes | Volumes are project data. Nothing in the dashboard deletes them. |
| A disk condition on Projects or Overview | The Needs-attention conditions are per project and locked at four (Phase 3 §6). The disk belongs to the server, not a project. Alerts cover the time the owner isn't looking. |

---

## 6. Decisions for the owner

- **D1 · Placement.** Phase 5 §6 puts *Checks* fourth, after the read-only settings.
  - **Recommendation:** place *Disk and backups* **second**, directly after GitHub App and before *Installation settings*.
  - Why: it is live state, and the settings are reference. The governing rule puts state first.
  - This reorders a locked list, so it needs the owner's word. If declined, the section goes fourth as reserved.
- **D2 · Auto-refresh while running.** Q-S2 names Overview and Projects only.
  - **Recommendation:** allow the 10 s refresh on `/system` only while a clean-up or backup started from the page is running.
  - The page has no text fields a refresh could discard.
  - Alternative: no refresh, plus a *Refresh* link.
- **D3 · A primary action when there is no backup.** The page has no primary action once GitHub is connected.
  - **Recommendation:** when *Last backup* is *None yet* or older than 26 h, **Back up now** becomes the page's primary. In every other state it is secondary.
  - This follows the governing rule: the next useful action is obvious. It's one primary per view, and only when it matters.
- **D4 · Section name.**
  - **Recommendation:** "Disk and backups".
  - Alternative: "Maintenance", which is broader but says less.

---

## 7. Engine work this needs (implementation, not design)

- **E1 · Requests go to the worker, not the web process [small].**
  - The dashboard (`api` container) does not mount the backup directory, and a backup takes minutes; a web request must not hold them.
  - Instead, a `maintenance_runs` table holds: kind (`cleanup` / `backup`), requested_at, started_at, finished_at, result (JSON) and error.
  - The worker claims a requested row in its job loop.
  - Scheduled runs write a row too, so *Last clean-up* and *Last backup* show real results, whoever started them.
  - The test alert is sent from the web process directly: it takes a second and needs only the webhook URL.
- **E2 · Measure the disk the images are on [small].** This gap exists today, independent of the page.
  - Now: the disk alert (`monitor._check_disk`), the pre-build guard (`housekeeping.ensure_room`) and `deploypro doctor` all measure `build_root`, which is on the server disk. Since images moved to the Hetzner Volume (`/var/lib/containerd`), nothing measures the Volume. It could fill up with no alert and no guard.
  - Fix: a setting, `DEPLOYPRO_IMAGE_STORE_DIR`, whose host path is mounted read-only into the worker and api.
  - That path is measured by the alert, the guard (a build needs room on both), doctor and this page.
  - When it is on the same filesystem as `build_root`, the two collapse into one row.
- **E3 · Build cache size.** Read from what the clean-up already measures to apply the cap; do not run a second `docker` call per page view. Cache the last measurement with its time.
- **E4 · Backup size and count.** From the newest backup's `manifest.json` and the directory listing. Both are read by the worker and stored in the E1 row, because the api doesn't mount the directory.
- **Tests:**
  - each §3 state renders its mark and copy
  - a second request while one runs is refused
  - the refresh is present only while running
  - the webhook URL never appears in the page
  - the E2 guard fails a build when the image disk, not the server disk, is short
