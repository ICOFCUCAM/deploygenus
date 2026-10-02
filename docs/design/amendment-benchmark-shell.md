# DeployPro — Amendment: the whole dashboard takes the benchmark's look

**Status:** **DECIDED 2026-10-02 by the owner.** After the Overview amendment (`amendment-overview-preview.md`) shipped, the owner said the page was "still not close to the benchmark" and chose:
1. the **whole dashboard** gets the benchmark's look, not only the Overview;
2. anything in the benchmark **without real data behind it is left out**.

**Amends:** Phase 4 §1 ("must not resemble a Vercel imitation"), §3 (paper background, hairline-only surfaces) and §4 ("no tinted chips"), and Phase 5's frame (top bar plus a per-project text menu). Everything else stands: blue is still the action colour only, every status still has its shape and its word, the deployment line is unchanged, and every page still works without JavaScript.

---

## 1. The frame

| Benchmark | Here |
|---|---|
| Dark left sidebar | **Yes.** Projects, New project, and under *Server*: System, GitHub. Sign out at the foot. On a phone it folds into a menu in the top bar. |
| Top bar | **Yes.** Breadcrumb (Projects › project) and a light/dark switch. The switch needs JavaScript and is hidden without it; the system setting applies until it is used. |
| Search, notifications, environment switcher | **No.** None has a backend. |
| Sidebar: Deployments, Domains, Logs, Monitoring, Settings across all projects | **No.** There are no cross-project pages for them. Each project's pages are in its tabs. |
| User and avatar at the foot | **No.** There is one owner and no profile. Sign out takes its place. |

## 2. The project header and tabs (every project page)

- App tile (the project's initial), name, address ↗, repository, production branch, a **Production** pill once something serves, the project's state as a pill (**Healthy** / **Needs attention** / **Not deployed**) and *Deployed n ago*.
- Tabs: **Overview · Deployments · Runtime · Configuration · Domains**. Runtime and Configuration keep their sub-pages as a second row of small tabs.
- **Left out:** the description line (DeployPro stores none), and the Logs and Monitoring tabs (logs live on each deployment; nothing records monitoring history).

The header costs each project page a few small queries (production, domains, recent deployments, jobs) so it says the same thing on every tab.

## 3. The Overview

Three columns at 1280px and wider: the picture and the deployment line, the production card, and a column of facts. Narrower, the facts move under the page; on a phone everything stacks in the amendment's order (picture, production, line, numbers, details, domains, runtime, deployments).

| Benchmark | Here | Why |
|---|---|---|
| Production card: full-width **Open live site**, then **Redeploy** | **Yes**, the second button is **Deploy {branch}** | It builds the branch head, which is what DeployPro's button does. |
| Stat tiles with icons and trend arrows | **Yes** | The trend compares the last 7 days with the 7 before, and is shown only when those days recorded something. |
| Response time, Uptime | **No** | Not recorded (as before). |
| Project details | **Yes**: repository, branch, build, created | |
| Region | **No** | Not known to DeployPro. |
| Infrastructure card (server, IP, SSH key, firewall, volumes, backups) | **As "Runtime"**: web, workers, jobs, storage mounts, resources, and a link to the System page | Only what DeployPro records per project. IP, SSH key and firewall are not. |
| Deployments table in a card, "View all" | **Yes** | |
| ⋯ menu per row | **No** | Each row's number opens the deployment, where its actions are. |
| Tablet preview | **No** | As decided in the Overview amendment. |

## 4. Colour

- Page ground `#F4F6F9` (light) / `#0F1216` (dark); cards are white / `#171B20` with 12px corners and a soft shadow.
- **Pills:** a status word on its own family's tint. Every word/tint pair passes 4.5:1, and `tests/test_design_tokens.py` checks them with the other pairs.
- Status words are sentence case (*Ready*, *Healthy*), as in the benchmark, instead of small capitals.
- The sidebar is dark in both themes; its text passes 4.5:1 on it.
- `scripts/check_marks.py` uses the new grounds and still passes: the marks are recognisable without colour.
