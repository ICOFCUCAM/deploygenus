# Gaps found launching DeployPro on a real server

Notes from following `docs/02-hetzner-cloudflare.md` and `scripts/install.sh`
on a fresh server, end to end. Each entry says what went wrong and what was
done about it.

| # | Where | What happened | Status |
| --- | --- | --- | --- |
| 1 | Guide, step 4 | The guide said to add `DEPLOYPRO_BRANCH=claude/tender-archimedes-libref`. That branch was merged into `main` and deleted, so following the note made `git clone --branch` fail. | Fixed: note removed; the installer defaults to `main`. |
