# DeployPro — Phase 4 Design System: Quiet Infrastructure

**Status:** **LOCKED 2026-09-24** (revision 2). Later phases build on this system; they don't reopen it.
**Direction:** originated by the owner. Claude validated it against the engine, the locked Phase 2 (`deploypro-phase2-final.md`), the locked Phase 3 (`deploypro-phase3-ux-states.md`), the server-rendered/no-build-step constraints, WCAG 2.2 AA, the existing dark mode and real deployment data.
**This revision:** applies the owner's decisions C1–C10 and the six-shape decision exactly. The direction is unchanged. Claude chose the colour values only where the owner delegated them (the status colours, the control borders, the dark-mode blue), within the owner's named families.

**Implementation rules (locked principle):**
> Blue identifies DeployPro actions. Semantic colours identify system conditions. Shape identifies state. Words identify meaning. Structure identifies hierarchy.

---

## 1. Visual thesis

DeployPro should feel like serious infrastructure presented with editorial clarity.

**Personality:** precise · calm · technical · confident · deliberate. The interface communicates control, not excitement.

**It must not resemble:**
- a generic SaaS dashboard
- an AI dashboard
- a Vercel imitation
- a glassmorphism product
- a terminal wrapped in a web page

The identity comes from what DeployPro is: code moving through a controlled path until it becomes production.

## 2. The deployment line (the same everywhere)

`SOURCE → BUILD → DEPLOY → PRODUCTION`. These are exactly the locked Phase 2 §2G nodes. The dashboard and the public site both use this model. The public site may draw it larger and more expressively. **No surface may add or remove a node.**

| Node | Engine record | Drawn as |
|---|---|---|
| SOURCE | `created_at`, commit, branch (the Queued interval lives here) | node |
| BUILD | `started_at` → `built_at` | timed node |
| DEPLOY | `built_at` → `ready_at`; **HEALTH is drawn inside DEPLOY** (it's recorded only as a log line) | timed node |
| PRODUCTION | `projects.production_deployment_id`; the switch time isn't recorded | **marker**, not a timed node |
| Rollback | pointer moves to an older ready deployment | **transition**, not a status |

**Line states (locked in Phase 2):**
- complete: continuous
- in progress: the active segment is highlighted; a static form under reduced motion
- failed: broken at the step that actually failed, with the stored error beneath it
- cancelled: ends at SOURCE
- preview: ends at DEPLOY with a preview end-point and never reaches PRODUCTION

The line appears in:
- the deployment detail page
- the history rows, as a compressed track
- the Overview: production as the end-point, rollback as the line returning
- build progress
- the public site

When the engine later records HEALTH and PRODUCTION times, those nodes gain times. The line isn't redesigned.

## 3. Colour: identity and surfaces

The old green accent is retired. **DeployPro blue is the identity/action colour only. It is never a status colour.** It's used for:
- the primary action button
- links
- focus rings
- selected navigation

| Role | Light | Dark |
|---|---|---|
| Background (Paper / BG) | `#F7F5F0` | `#111315` |
| Surface | `#FFFFFF` | `#181B1E` |
| Elevated surface | — | `#202428` |
| Primary text (Ink) | `#171A1D` | `#F2F1ED` |
| Graphite (secondary headings) | `#2B3035` | — (uses primary text) |
| Muted text | `#62676B` | `#A8AAA6` |
| **Structural hairline** (dividers, rules; decorative) | `#D8D6D0` | `#34393D` |
| **Interactive control boundary** (inputs, selects, secondary buttons, checkboxes) | `#8A8780` | `#71777C` |
| **DeployPro blue** (links, primary button fill, focus ring) | `#2457D6` | `#7C9DF6` ← changed from `#5B82F1` (C8) |
| Blue hover | `#1B46B4` | `#A3BAF9` |
| Text on the blue button | `#FFFFFF` | `#111315` ← dark text (C8) |

**Contrast (WCAG 2.2, verified).** Text needs 4.5:1. UI boundaries and marks need 3:1 (SC 1.4.11).

| Pair | Light | Dark (BG / Surface / Elevated) | Need | Result |
|---|---|---|---|---|
| Primary text | 16.04 / 17.47 (paper / surface) | 16.47 / 15.30 / 13.82 | 4.5 | pass |
| Muted text | 5.25 / 5.72 | 7.95 / 7.38 / 6.67 | 4.5 | pass |
| Blue as link text | 5.65 / 6.16 | 7.10 / 6.60 / 5.96 | 4.5 | pass (dark `#5B82F1` was 4.39 on Elevated: fixed) |
| Primary button text on blue | 6.16 (white) | 7.10 (`#111315`) | 4.5 | pass (dark white-on-`#5B82F1` was 3.55: fixed) |
| Primary button hover text | 8.13 | 9.71 | 4.5 | pass |
| Blue button edge vs page | 5.65 | 7.10 / 6.60 / 5.96 | 3 | pass |
| Focus ring vs page | 5.65 / 6.16 | 7.10 / 6.60 / 5.96 | 3 | pass |
| **Control boundary** | 3.29 / 3.58 | 4.11 / 3.81 / 3.45 | 3 | pass (the old borders were 1.33–1.48: fixed, C6) |
| Structural hairline | 1.33 / 1.45 | 1.59 / 1.48 / 1.34 | — | decorative, exempt; **never the only boundary of a control** |

**Focus:** a 2px ring in DeployPro blue with a 2px offset. Its contrast is measured against the page behind the offset. The ring and the blue button share a colour (1.00:1), so the offset is **required**, not optional. Every interactive element has a visible `:focus-visible` state.

Surfaces barely differ from the background (Paper/Surface 1.09, BG/Surface 1.08). They're separated by hairlines and spacing, as §10 intends, never by fill alone.

## 4. Semantic status colours

Four families. Colour is always **supplementary**: every status also has its shape (§5) and its word. Blue is never among them (C1).

| Family | Light | Dark | Text on Paper / Surface | Text on BG / Surface / Elevated |
|---|---|---|---|---|
| **Success** (green) | `#1D7A45` | `#5FBF8A` | 4.91 / 5.35 | 8.25 / 7.66 / 6.92 |
| **Caution** (amber) | `#8F5B00` | `#E2A73D` | 5.26 / 5.73 | 8.71 / 8.09 / 7.31 |
| **Failure** (red) | `#B3261E` | `#F2877A` | 6.00 / 6.54 | 7.56 / 7.02 / 6.34 |
| **Neutral** | `#62676B` (= muted) | `#A8AAA6` (= muted) | 5.25 / 5.72 | 7.95 / 7.38 / 6.67 |

- All pass 4.5:1, so a status **word** can be set in its family colour, and the **mark** passes 3:1 with room to spare.
- Status colours are used for marks and status words only. They're never used for backgrounds, tinted chips, borders or decoration.
- **Destructive button:** red fill.

  | Mode | Fill | Text | Contrast | Hover fill | Hover contrast |
  |---|---|---|---|---|---|
  | Light | `#B3261E` | white | 6.54 | `#8F1E17` | 8.88 |
  | Dark | `#F2877A` | `#111315` | 7.56 | `#F6A69C` | 9.62 |

- **Greyscale note (verified, intended):** the four families have near-equal lightness (for example light green vs amber 1.07:1). Greyscale readability therefore rests on **shape and word**, as Phase 2 §2H requires. That's why §5 is mandatory, not decorative.

## 5. Shapes and the complete status matrix

**Deployment outcome: six marks, distinguishable by shape alone** (owner decision):

| # | State | Mark concept | Family |
|---|---|---|---|
| 1 | Queued | open / outlined | neutral |
| 2 | Building | active process mark (static under reduced motion) | neutral |
| 3 | Deploying | directional / forward mark | neutral |
| 4 | Ready | solid mark | success |
| 5 | Failed | error geometry | failure |
| 6 | Cancelled | cancelled geometry | neutral |

Plus **one caution mark** for attention states. It's distinct from Failed, so "needs attention" never reads as "failed". Other domains reuse these seven marks by meaning. No new colours are introduced.

**Words:** status words are real text, styled in small capitals via CSS (`text-transform`). The source text is sentence case, so screen readers don't spell out letters. Marks are `aria-hidden`; the word carries the meaning.

**The complete matrix** (Phase 2 §2H domains; the families are the owner's C2 decision):

| Domain | State → family (mark) |
|---|---|
| Deployment outcome | Queued → neutral (queued) · Building → neutral (building) · Deploying → neutral (deploying) · Ready → success (ready) · Failed → failure (failed) · Cancelled → neutral (cancelled) |
| **Kind** (not a colour state) | **Production branch** / **Preview**: a word plus a kind marker in the text colour, never a status colour |
| **Current production** (not a colour state) | **Current production**: the word plus a distinct marker in the text colour, on exactly one deployment per project |
| Container (Ready only) | Running → success · Stopped → neutral · Removed → neutral (**[small]**; until the engine can tell, shown as Stopped per Phase 3) |
| Worker | Running n/n → success · Below wanted → caution (**[small]**) · Paused → neutral · Finishing work → neutral (**[small]**) |
| Job run | Pending → neutral · Running → neutral (building mark) · Succeeded → success · Failed → failure · Timed out → caution · Skipped → neutral |
| Domain | Verified → success · Waiting for DNS → caution · Pointing elsewhere → caution · Platform domain doesn't resolve → caution |
| Project (derived) | Healthy → success · Needs attention → caution · Not deployed → neutral · *Down* ⛔ not available (needs stored health; never shown) |
| Overview production block | Serving (current production, running) → success · **"Production is down: its container is gone."** (Phase 3 S-PROD-MISSING) → failure |

**Mappings derived from the C2 families (confirmed by the owner at locking):**
1. **"Finishing work" is neutral.** C2 allows caution "where user attention may matter". The engine records no condition where finishing work needs attention: the stop timeout ends it by itself. The case that does need attention is already covered by *Below wanted*.
2. **"Production failure" (red)** is applied to the S-PROD-MISSING headline on the Overview. The same project's summary status stays **Needs attention** (amber), because locked Phase 2 lists "production container missing" as one of the four derived Needs-attention conditions. The summary level stays amber, and the specific headline is red.
3. The two Phase 3 domain states the C2 list didn't name (*pointing elsewhere*, *platform domain doesn't resolve*) are caution, as DNS problems the owner can act on.

**Shape verification (Phase 6, before merge):**
- each mark at its rendered size, colour removed (greyscale screenshot), must be identifiable
- specifically **Queued vs Ready** (outline vs fill must differ at small size)
- **Failed vs Cancelled** (the two must not share a cross)
- **Failed vs the caution mark**

## 6. Three separate dimensions

Never collapsed into one status (Phase 3 rule 8):

| Dimension | Values | Applies to |
|---|---|---|
| Deployment state | the six outcomes | every deployment |
| Production relationship | **Production branch** / **Preview** (kind) and **Current production** (marker) | every deployment / exactly one |
| Runtime | Running / Stopped / Removed | Ready deployments only |

The bare word "PRODUCTION" is never used where it could be ambiguous (C3). Examples:
- `READY · PRODUCTION BRANCH · CURRENT PRODUCTION · RUNNING`: the one serving deployment
- `READY · PRODUCTION BRANCH · STOPPED`: an older rollback target
- `READY · PREVIEW · RUNNING`

Kind is derived from the *current* production branch until kind is stored (**[small]**). Phase 5 must not present it as history.

## 7. Typography

- **IBM Plex Sans:** headings, navigation, labels, buttons, explanations, descriptions, forms.
- **IBM Plex Mono**, selectively, for:
  - commit SHAs
  - identifiers
  - ports
  - code-like values
  - timestamps and durations where alignment matters
  - container and image identifiers
  - technical configuration
- **Files:** four (Sans 400/600, Mono 400/600), WOFF2, Latin subset, `font-display: swap`. They're served by DeployPro itself, never from a third party. The OFL licence file ships with them.
- **Fallbacks:**
  - Sans: `"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`
  - Mono: `"IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace`

  Characters outside the Latin subset (commit messages, author names) fall back to these.
- **Phase 6 constraint:** `pyproject.toml` package-data is `web/static/*`, which doesn't include subfolders. Fonts in a subfolder need that pattern extended, or the dashboard ships unstyled.

## 8. Type scale

| Role | Size |
|---|---|
| Display (public site only) | 40–48px |
| Page title | 28–32px |
| Section heading | 20–22px |
| Body | 15–16px |
| Small / metadata | 13–14px |
| Mono | 13–14px |

Exact values are tuned in Phase 5 against real layouts. Sizes are set in `rem` so browser zoom and user font settings apply. Large type sets hierarchy, not decoration.

## 9. Grid and spacing

4px base, 8px dominant rhythm. Scale: 4 · 8 · 12 · 16 · 24 · 32 · 40 · 48 · 64 · 80. The dashboard is quiet, not empty. The public site uses the larger steps.

## 10. Surfaces and borders

- **Structural devices:** whitespace, alignment, hairlines, restrained borders, typography, grouping.
- Cards appear only where they mark a meaningful boundary.
- No default heavy shadows, no floating-card piles, no excessive rounding.
- **Radius:** modest on controls and contained panels; square or near-square for page structure. Values are tested in Phase 5.
- **Two border roles (C6):**
  - **structural hairline** (quiet, decorative)
  - **interactive control boundary** (≥3:1)

  A hairline is never the only visible edge of an interactive control.

## 11. Buttons and controls (C4)

**Rule:** every view has **one** contextually correct primary action, decided by its current state. The Phase 3 action matrix is followed exactly. Destructive actions are never primary.

| Style | Treatment |
|---|---|
| Primary | DeployPro blue fill (§3) |
| Secondary | neutral surface, control boundary |
| Quiet | text link in blue |
| Destructive | red (§4), only in its separated zone |

**Primary action by state (from Phase 3, not changed here):**

| View / state | Primary |
|---|---|
| Overview, production serving / empty / after rollback | **Deploy {branch}** (never a bare "Deploy") |
| Overview, first or newer deploy running or failed | **View deployment** / **View #N+1** |
| Overview, production missing | **Roll back to #M** if a candidate exists, else **Redeploy #N** |
| Deployment: Queued | **Cancel** |
| Deployment: Building / Deploying | none |
| Deployment: Current production | **Redeploy** |
| Deployment: Ready, newer than production | **Make production** |
| Deployment: Ready, older than production | **Roll back to #N** |
| Deployment: Ready · Removed | **Redeploy** (only action) |
| Deployment: Failed / Cancelled | **Redeploy** |

Phase 3 Q-S1: **Deploy {branch}** stays available while a build is running, with a note. Disabled controls stay visibly disabled with a reason in text.

## 12. Deployment rows (C5)

The list structure is kept. Rows are not cards. Every row carries what Phase 3 §4 requires:

```
#19   ● READY   PRODUCTION BRANCH   ◆ CURRENT PRODUCTION

8d3a2da8   claude/adoring-mayer-yfghmg
Publication formats: one composition, four shapes, and the meaning survives

06:47 · 2m 18s · RUNNING
```

| Element | Rule |
|---|---|
| `#N` | always |
| Outcome mark + word | always |
| Kind (Production branch / Preview) | always |
| Current production | only on the one serving deployment |
| Short commit (Mono) + branch | always |
| Commit message | only when `git_message` exists. **When it's absent (dashboard, CLI and import deploys today), the slot isn't rendered: branch and short commit only, never blank or "—".** |
| Time | exact time, with a relative form where Phase 5 places it |
| Duration / elapsed | **duration** when finished (`started_at` → `finished_at`); **elapsed** while in progress |
| Runtime | Ready rows only: Running / Stopped (Removed when **[small]** lands) |
| Compressed line | how far it got (§2) |

The row distinguishes identity, state, production relationship, source, message, time and runtime without noise.

## 13. Forms and configuration

Forms are precise instruments:
- clear labels, and explanatory descriptions linked with `aria-describedby`
- **control boundaries at ≥3:1** (§3)
- visible scope
- explicit Save/Apply
- no refresh on configuration pages (Q-S2)

**Environment scopes are exactly the engine's three:** All environments · Production only · Preview only. A specific scope overrides All; this is the engine rule since commit `10793a7`.

**Q-S3:** *"Changed since production was built — redeploy to apply"*, with **Redeploy**. Form errors are shown as text next to the field and in a summary, never by colour alone.

## 14. Destructive actions

**Delete project** sits at the bottom of Configuration (Phase 2 decision 1), visually separated. It requires typing the project name (Q-S5). The copy states exactly what `service.delete_project` does:
- it can't be undone
- deployment history is deleted
- routes are removed, and its containers are stopped and removed
- **stored files (volumes) stay on the server**

## 15. Icons

- One shared inline-SVG set, rendered by a Jinja macro. No icon font, no external service, no build step.
- **Style:** small, geometric, quiet, consistent stroke, subordinate to text.
- **Vocabulary:**
  - project, repository, branch, deployment
  - server, worker, job
  - clock, settings, domain, storage
  - warning, external link, rollback, delete
- Decorative icons are `aria-hidden`. Any icon-only control has an accessible name.
- Icons aren't added just to decorate navigation.

## 16. Motion (C7)

Motion only shows **real, current** activity:
- the active segment of a building or deploying deployment
- live log activity
- expanding operational detail (`<details>`)

It's CSS only; no JavaScript animation. Every animation has a static equivalent under `prefers-reduced-motion`. That mechanism already exists in `deploypro.css`.

**No production-switch animation.** Promote and rollback finish inside one form POST before the response arrives (Phase 3 §7). The returned page simply shows the new state:
> previous deployment → current production

No transition is invented.

**Truth constraint:** an in-progress indicator appears only when the engine reports that state. On the live deployment page, the line's active step changes on page load: the final reload, or meta refresh under Q-S2. The page never simulates progress between loads.

No decorative or perpetual animation.

## 17. Dark mode (C8)

Dark mode is kept as the second tested expression of the same identity (deep graphite + light text + blue signal). It still follows `prefers-color-scheme`.

**Changes from the proposal:**
- dark blue `#7C9DF6` with **dark text** (`#111315`) on the primary button
- blue links and the focus ring pass on all three dark surfaces
- the control boundary is `#71777C`

Status families keep the same meaning and marks in both modes. Every pair is tested (§3, §4).

## 18. Public website (C9)

- **Same identity, lower density.** Public voice: **Deploy on hardware you own.** Dashboard voice (locked): **What is running?**
- **The public line is §2's line exactly:** SOURCE → BUILD → DEPLOY → PRODUCTION, with HEALTH inside DEPLOY, PRODUCTION as a marker, rollback as a transition, preview ending at DEPLOY.
- It may be drawn large, vertically, step by step, with explanatory copy. "Your code" and "your server" belong **in the copy**, not as nodes.
- **Explains:**
  - what DeployPro is
  - self-hosted, Git-based deployment
  - automatic detection
  - Dockerfile builds
  - previews, production and rollback
  - workers and scheduled jobs
  - persistent storage
  - domains and HTTPS
  - owning your infrastructure
- **Framework wording (locked):** "DeployPro detects your application's framework automatically." Never "supported". Dockerfile builds are the proven path.
- **Must not claim (Phase 2):**
  - preview protection
  - teams or multiple users
  - metrics
  - runtime logs
  - serverless or edge functions
  - multiple servers

## 19. Mobile (C3, C10)

Same hierarchy, recomposed. Current production deployment on a phone:

```
#19
● READY
PRODUCTION BRANCH
◆ CURRENT PRODUCTION
RUNNING

8d3a2da8
claude/adoring-mayer-yfghmg
commit message (only when recorded)

DEPLOYMENT LINE

[ Redeploy ]   Open ↗
```

- No generic **Rollback** button anywhere.
- A rollback control always names its target (**Roll back to #N**). It appears only when a valid candidate exists: on the Overview, and on that older ready deployment's own page.
- Dense detail collapses behind `<details>`.
- Desktop, tablet and phone all work without JavaScript.

## 20. Anti-style list

Avoid becoming:
- Vercel-like or Linear-like by default
- generic Tailwind SaaS
- glassmorphism
- neon cyberpunk
- an AI-gradient aesthetic
- excessive rounded cards
- excessive shadows
- giant decorative icons
- charts without operational purpose
- colour everywhere
- animation for its own sake
- terminal cosplay
- "developer dark mode" as the whole identity

The product looks quietly authoritative.

## Final principle

DeployPro takes something you own, moves it through a controlled deployment path, and leaves you in control of what becomes production. **That is the identity**: not the colour, not the font, not the cards.

---

## Re-validation (revision 2)

| § | Verdict |
|---|---|
| 1 | Compatible |
| 2 | Compatible: identical to the Phase 2 §2G nodes and line states |
| 3 | Compatible: every text pair ≥4.5, every boundary and focus ≥3 (C6, C8 resolved) |
| 4 | Compatible: blue removed from status (C1); all families pass in both modes |
| 5 | Compatible: the complete Phase 2 §2H set is covered (C2); geometry verification is a Phase 6 gate; **3 derived mappings to confirm** |
| 6 | Compatible: Production branch / Current production separated (C3) |
| 7 | Compatible: the package-data constraint is recorded for Phase 6 |
| 8, 9, 10 | Compatible |
| 11 | Compatible: the Phase 3 action matrix is kept exactly (C4) |
| 12 | Compatible: duration/elapsed added, message slot conditional (C5) |
| 13 | Compatible: control boundary ≥3:1; scopes match the engine |
| 14 | Compatible: copy matches `service.delete_project` |
| 15 | Compatible |
| 16 | Compatible: no switch animation (C7) |
| 17 | Compatible: dark primary button 7.10, links ≥5.96 (C8) |
| 18 | Compatible: same line, locked voices (C9) |
| 19 | Compatible: Redeploy primary, no generic Rollback (C10), terminology explicit |
| 20 | Compatible |

**No remaining conflicts.** The three derived mappings in §5 were confirmed by the owner at locking. One gate remains for Phase 6: verify the shape geometry (greyscale test, before merge).

**Locked summary:**

| Meaning | Treatment |
|---|---|
| User action / identity | DeployPro blue |
| Healthy / success | Green |
| Needs attention | Amber |
| Failure | Red |
| Ordinary operational / inactive state | Neutral |
| Production branch vs Preview | Explicit word + marker/context, not colour |
| Current production | Explicit marker, never confused with production branch |
| Status distinction | Word + shape; colour is supplementary |
| Focus | 2px blue ring with a mandatory 2px gap |
| Interactive borders | ≥3:1 |
| Decorative dividers | Stay quiet |
| Production switch | No invented animation |
| Primary action | Decided by the actual state and view |
| Deployment line | SOURCE → BUILD → DEPLOY → PRODUCTION |

Status colours depend on context: the same engine condition (production container missing) is red in the Overview headline and amber in the project summary, because each level answers a different question.

The **[small]** engine items referenced (Removed, Below wanted, Finishing work, stored kind) have Phase 3 fallbacks and don't block locking. The engine stays frozen.
